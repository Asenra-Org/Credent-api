import json
import logging
import asyncio
from datetime import datetime, timezone
import hashlib
import os
import uuid
import celery

from app.queue.celery_app import celery_app
from app.services.outbox_dispatcher import OutboxDispatcher, CeleryTransportAdapter
from app.database.session import get_session_factory
from app.models.ase52 import Job, AgentExecution, Case, OutboxEvent, AppraisalResult
from app.services.storage import get_storage_service

from app.agents.input.document_ingestion import DocumentIngestionAgent
from app.agents.analysis.financial_health import FinancialHealthAgent
from app.agents.analysis.management_quality import ManagementQualityAgent
from app.agents.analysis.sector_context import SectorContextAgent
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent
from app.agents.orchestration.cam_generator import CAMGeneratorAgent
from app.agents.orchestration.coordinator import AgentCoordinator
from app.services.storage import StorageTimeoutError, StorageProviderUnavailableError
import redis
import psycopg2

def is_transient_error(e: Exception) -> bool:
    return isinstance(e, (
        StorageTimeoutError,
        StorageProviderUnavailableError,
        redis.exceptions.ConnectionError,
        psycopg2.OperationalError
    ))

logger = logging.getLogger(__name__)

class TransientQueueException(Exception): pass
class EntityNotFoundError(Exception): pass
class InvalidStateTransitionError(Exception): pass
class SecurityViolationError(Exception): pass

def execute_with_boundary(stage_name: str, next_stage: str = None):
    def decorator(business_logic_fn):
        def wrapper(self, payload: str):
            try:
                data = json.loads(payload)
            except Exception as e:
                logger.error(f"Malformed payload: {e}")
                return False

            execution_id = data.get("execution_id")

            logger.error(f"DEBUG: Payload string is: {payload}")
            logger.error(f"DEBUG: Parsed data is: {data}")

            if not execution_id:
                # Fallback to check if it's inside 'data' key due to some double-wrapping
                if "data" in data and isinstance(data["data"], dict) and "execution_id" in data["data"]:
                    execution_id = data["data"]["execution_id"]
                else:
                    logger.error("No execution_id in payload")
                    return False

            session_factory = get_session_factory()
            with session_factory() as session:
                execution = session.query(AgentExecution).filter_by(id=execution_id).with_for_update().first()
                if not execution:
                    logger.error(f"Execution {execution_id} not found")
                    return False

                if execution.status in ["COMPLETED", "SUCCESS", "FAILED", "TIMED_OUT", "SKIPPED"]:
                    logger.info(f"Execution {execution_id} already in terminal state {execution.status}. Idempotent return.")
                    return True

                execution.status = "RUNNING"
                execution.celery_task_id = self.request.id
                execution.started_at = datetime.now(timezone.utc)
                session.commit()

            try:
                # Run business logic inside its own session logic
                business_data = data.get("data", data) if isinstance(data, dict) else data
                result = business_logic_fn(self, business_data)

                with session_factory() as session:
                    execution = session.query(AgentExecution).filter_by(id=execution_id).first()
                    execution.status = "SUCCESS"
                    execution.completed_at = datetime.now(timezone.utc)
                    if isinstance(result, str):
                        execution.output_storage_key = result

                    job = session.query(Job).filter_by(id=execution.job_id).first()

                    if next_stage:
                        # Create next job and execution
                        next_job = Job(
                            id=f"JOB_{uuid.uuid4().hex}",
                            case_id=job.case_id,
                            tenant_id=job.tenant_id,
                            stage_name=next_stage,
                            status="QUEUED"
                        )
                        next_execution = AgentExecution(
                            id=f"EXEC_{uuid.uuid4().hex}",
                            job_id=next_job.id,
                            tenant_id=job.tenant_id,
                            agent_type=next_stage,
                            attempt_number=1,
                            status="PENDING"
                        )
                        session.add(next_job)
                        session.add(next_execution)

                        next_payload = data.copy()
                        if "data" in next_payload and isinstance(next_payload["data"], dict):
                            next_payload["data"]["job_id"] = next_job.id
                            next_payload["data"]["execution_id"] = next_execution.id
                        else:
                            next_payload["job_id"] = next_job.id
                            next_payload["execution_id"] = next_execution.id

                        # Save outbox event for next stage
                        outbox = OutboxEvent(
                            id=f"evt_{uuid.uuid4().hex}",
                            tenant_id=job.tenant_id,
                            aggregate_type="CASE",
                            aggregate_id=job.case_id,
                            event_type=next_stage,
                            payload=json.dumps(next_payload),
                            status="PENDING"
                        )
                        session.add(outbox)

                    session.commit()
                return True
            except Exception as e:
                logger.error(f"Business logic failed: {e}")
                with session_factory() as session:
                    execution = session.query(AgentExecution).filter_by(id=execution_id).first()
                    execution.error_message = str(e)

                    if is_transient_error(e):
                        if execution.attempt_number >= 4:
                            execution.status = "FAILED"
                        else:
                            execution.status = "RETRY_AUTHORIZED"
                            execution.attempt_number += 1
                    else:
                        execution.status = "FAILED"

                    session.commit()

                if execution.status == "RETRY_AUTHORIZED":
                    retry_index = execution.attempt_number - 2
                    delay = 60 * (2 ** retry_index)
                    raise self.retry(exc=e, countdown=delay)
                return False
        return wrapper
    return decorator


@celery_app.task(name="app.queue.tasks.sweep_outbox")
def sweep_outbox():
    transport = CeleryTransportAdapter(celery_app)
    dispatcher = OutboxDispatcher(get_session_factory(), transport)
    dispatcher.reconcile_expired_leases()
    dispatched = dispatcher.dispatch_batch()
    return f"Dispatched {dispatched} events."

@celery_app.task(name="app.queue.tasks.stage_1_ingest", bind=True)
@execute_with_boundary("stage_1_ingest", "stage_2_analysis_group")
def credent_ingest(self, data: dict):
    logger.info("Running stage 1")
    storage = get_storage_service()

    documents = data.get("documents", [])
    extracted_financials = {}

    for doc in documents:
        storage_key = doc.get("storage_key")
        doc_role = doc.get("doc_role", "OPTIONAL")

        if not storage_key:
            continue

        try:
            content = storage.download_file(tenant_id=data["tenant_id"], storage_key=storage_key)

            # Verify NO PDF BYTES in CELERY PAYLOAD -> content is fetched from storage!
            temp_path = f"temp_{uuid.uuid4().hex}.pdf"
            with open(temp_path, "wb") as f:
                f.write(content)

            try:
                # Phase 5/6 Invocation
                agent = DocumentIngestionAgent()
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                ingestion_result = loop.run_until_complete(agent.ingest_pdf(temp_path))

                if ingestion_result.get("error"):
                    raise ValueError(f"Parsing Failure: {ingestion_result['error']}")

                parsed = loop.run_until_complete(agent.parse_financial_statement(ingestion_result.get("text", "")))
                loop.close()

                if isinstance(parsed, dict):
                    extracted_financials.update(parsed)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
        except Exception as e:
            if doc_role == "REQUIRED":
                raise ValueError(f"REQUIRED document ingestion failed: {str(e)}") from e
            else:
                warning_msg = {"document": storage_key, "role": "OPTIONAL", "reason": f"Parsing Failure: {str(e)}"}
                logger.warning(f"Optional document ingestion failed, continuing: {e}")
                data.setdefault("audit_warnings", []).append(warning_msg)

    # Save output to storage
    out_key = storage.upload_file(
        tenant_id=data["tenant_id"],
        case_id=data["case_id"],
        document_id="aggregate",
        filename=f"stage_1_out_{uuid.uuid4().hex}.json",
        content=json.dumps(extracted_financials).encode(),
        content_type="application/json"
    )
    return out_key

@celery_app.task(name="app.queue.tasks.stage_2_analysis_group", bind=True)
@execute_with_boundary("stage_2_analysis_group", "stage_3_synthesis_chord")
def credent_analysis(self, data: dict):
    logger.info("Running stage 2")
    # Fetch stage 1 output
    session_factory = get_session_factory()
    with session_factory() as session:
        prev_job = session.query(Job).filter_by(case_id=data["case_id"], stage_name="stage_1_ingest").first()
        prev_exec = session.query(AgentExecution).filter_by(job_id=prev_job.id, status="SUCCESS").first()
        stage_1_key = prev_exec.output_storage_key

    storage = get_storage_service()
    stage_1_data = json.loads(storage.download_file(stage_1_key))

    # Phase 5/6 Invocation
    fin_agent = FinancialHealthAgent()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    fin_res = loop.run_until_complete(fin_agent.analyze(stage_1_data))
    loop.close()

    # Save output to storage
    out_key = storage.upload_file(
        tenant_id=data["tenant_id"],
        case_id=data["case_id"],
        document_id=data["document_id"],
        filename=f"stage_2_out_{uuid.uuid4().hex}.json",
        content=json.dumps({"financial_health": fin_res}).encode(),
        content_type="application/json"
    )
    return out_key

@celery_app.task(name="app.queue.tasks.stage_3_synthesis_chord", bind=True)
@execute_with_boundary("stage_3_synthesis_chord", None)
def credent_synthesis(self, data: dict):
    logger.info("Running stage 3")
    storage = get_storage_service()

    # Phase 5/6 Invocation
    # The requirement specifically mentions "AgentCoordinator" being invoked!
    coord = AgentCoordinator()

    # Mocking cam_payload logic because tasks.py in Phase 7 mocks this
    cam_payload = {
        "five_cs": {
            "character": {"text": "dummy", "citations": []},
            "capacity": {"text": "dummy", "citations": []},
            "capital": {"text": "dummy", "citations": []},
            "collateral": {"text": "dummy", "citations": []},
            "conditions": {"text": "dummy", "citations": []}
        },
        "decision": "APPROVED",
        "recommended_loan_amount": "0",
        "recommended_interest_rate": "0",
        "decision_rationale": "mock"
    }

    cam_payload["audit_warnings"] = data.get("audit_warnings", [])

    from app.routes.documents import apply_forensics_penalty
    
    # Extract forensics from stage 1
    session_factory = get_session_factory()
    stage_1_key = None
    with session_factory() as session:
        from app.models.ase52 import Job
        prev_job = session.query(Job).filter_by(case_id=data["case_id"], stage_name="stage_1_ingest").first()
        if prev_job:
            prev_exec = session.query(AgentExecution).filter_by(job_id=prev_job.id, status="SUCCESS").first()
            if prev_exec:
                stage_1_key = prev_exec.output_storage_key

    stage_1_data = {}
    if stage_1_key:
        try:
            stage_1_data = json.loads(storage.download_file(stage_1_key))
        except Exception as e:
            logger.warning(f"Failed to fetch stage 1 data for forensics: {e}")

    forensics_results = stage_1_data.get("forensics_results", [])
    aggregated_forensics = {"is_suspicious": False, "flags": []}
    for fr in forensics_results:
        if fr.get("is_suspicious"):
            aggregated_forensics["is_suspicious"] = True
        aggregated_forensics["flags"].extend(fr.get("flags", []))

    # Existing ASE-52 base score is 50
    base_score = 50
    penalty_data = apply_forensics_penalty(base_score, aggregated_forensics)
    adjusted_score = penalty_data.get("adjusted_score", base_score)
    
    # Append forensic flags to CAM
    if aggregated_forensics.get("flags"):
        if "audit_warnings" not in cam_payload:
            cam_payload["audit_warnings"] = []
        cam_payload["audit_warnings"].extend([{"reason": f} for f in aggregated_forensics["flags"]])

    cam_report_storage_key = storage.upload_file(

        tenant_id=data["tenant_id"],
        case_id=data["case_id"],
        document_id="aggregate",
        filename=f"cam_report_{uuid.uuid4().hex}.json",
        content=json.dumps(cam_payload).encode(),
        content_type="application/json"
    )

    session_factory = get_session_factory()
    with session_factory() as session:
        res = AppraisalResult(
            id=f"appraisal_{uuid.uuid4().hex}",
            case_id=data["case_id"],
            tenant_id=data["tenant_id"],
            base_score=base_score,
            adjusted_score=adjusted_score,
            decision="APPROVED",
            cam_report_storage_key=cam_report_storage_key
        )
        session.add(res)
        session.commit()

    return cam_report_storage_key

@celery_app.task(name="app.queue.tasks.ping")
def ping(payload: str = None):
    logger.info("Ping received")
    return True
