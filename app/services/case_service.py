import json
import uuid
import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.core.exceptions import TenantIsolationError
from app.models.ase52 import Case, OutboxEvent, Job, AgentExecution, Document

def get_release_tag() -> str:
    return "release_1"

def create_case_with_outbox_event(
    session: Session,
    tenant_id: str,
    borrower_name: str,
    total_loan_amount: float,
    company_type: str,
    deduplication_key: str,
    case_id: Optional[str] = None,
    document_id: Optional[str] = None,
    storage_key: Optional[str] = None
) -> Case:
    """
    Creates a new Case and enqueues an outbox event in the EXACT SAME atomic database transaction.
    Guarantees that business state mutation and outbox event creation commit together or rollback together.
    """
    if not tenant_id or not tenant_id.strip():
        raise TenantIsolationError("Tenant identity parameter is required.")
        
    actual_case_id = case_id if case_id else f"case_{uuid.uuid4().hex}"
    actual_doc_id = document_id if document_id else f"doc_{uuid.uuid4().hex}"
    job_id = f"JOB_{uuid.uuid4().hex}"
    exec_id = f"EXEC_{uuid.uuid4().hex}"
    event_id = f"evt_{uuid.uuid4().hex}"
    
    # 1. Create Case
    case = Case(
        id=actual_case_id,
        tenant_id=tenant_id,
        borrower_name=borrower_name,
        status="INITIATED"
    )
    session.add(case)
    
    # 2. Create Document
    if storage_key:
        doc = Document(
            id=actual_doc_id,
            case_id=actual_case_id,
            tenant_id=tenant_id,
            filename=storage_key.split('/')[-1] if '/' in storage_key else storage_key,
            storage_key=storage_key,
            doc_role="REQUIRED",
            status="ACTIVE"
        )
        session.add(doc)
        
    # 3. Create Job
    job = Job(
        id=job_id,
        case_id=actual_case_id,
        tenant_id=tenant_id,
        stage_name="stage_1",
        status="QUEUED"
    )
    session.add(job)
    
    # 4. Create AgentExecution
    execution = AgentExecution(
        id=exec_id,
        job_id=job_id,
        tenant_id=tenant_id,
        agent_type="stage_1_agent",
        attempt_number=1,
        status="PENDING"
    )
    session.add(execution)
    
    # 5. Create OutboxEvent
    payload = {
        "tenant_id": tenant_id,
        "case_id": actual_case_id,
        "job_id": job_id,
        "document_id": actual_doc_id if storage_key else None,
        "storage_key": storage_key,
        "borrower_name": borrower_name,
        "total_loan_amount": total_loan_amount,
        "company_type": company_type
    }
    
    # Explicitly do NOT put PDF bytes in payload
    outbox_event = OutboxEvent(
        id=event_id,
        tenant_id=tenant_id,
        aggregate_type="CASE",
        aggregate_id=actual_case_id,
        event_type="CASE_CREATED",
        payload=json.dumps(payload),
        status="PENDING",
        deduplication_key=deduplication_key if deduplication_key else f"dedup_{actual_case_id}",
        attempt_count=0,
        release_tag=get_release_tag()
    )
    session.add(outbox_event)
    
    # Session is flushed to assert constraints and atomic bounds.
    # The commit() is handled by the caller (get_db_session() context manager).
    session.flush()
    
    return case
