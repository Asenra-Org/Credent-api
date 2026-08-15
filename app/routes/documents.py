# =============================================================================
# CREDENT — Document Ingestion & PDF Forensics Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import shutil
import uuid
import pikepdf
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.agents.input.document_ingestion import DocumentIngestionAgent

router = APIRouter()

# ---------------------------------------------------------------------------
# Forensics Penalty — AI-A-W4
# Deduct exactly this many points from base_score when document tampering
# is detected by the pikepdf forensics layer.
# Declared as a module-level constant so it can be imported and asserted
# in tests without magic numbers, and adjusted by risk policy without
# touching business logic.
# ---------------------------------------------------------------------------
FORENSICS_PENALTY: int = 15

def run_pdf_forensics(file_path: str):
    """
    Scans a PDF's metadata to detect potential tampering or unnatural original
    """
    report = {
        "is_suspicious": False,
        "flags": [],
        "metadata": {}
    }

    try:
        with pikepdf.Pdf.open(file_path) as pdf:
            meta = pdf.docinfo

            # 1. Extract metadata
            creator = str(meta.get('/Creator', 'Unknown'))
            producer = str(meta.get('/Producer', 'Unknown'))

            report["metadata"] = {
                "creator": creator,
                "producer": producer,
            }

            # 2. Tamper Logic: Check for image editors
            suspicious_tools = ["photoshop", "illustrator", "canva", "nitro", "gimp", "inkscape"]
            for tool in suspicious_tools:
                if tool in creator.lower() or tool in producer.lower():
                    report["is_suspicious"] = True
                    report["flags"].append(f"UNNATURAL_SOURCE: Created via {tool}")

            # 3. Modification Logic
            mod_date = str(meta.get('/ModDate', ''))
            creation_date = str(meta.get('/CreationDate', ''))

            if mod_date and creation_date and mod_date != creation_date:
                 report["is_suspicious"] = True
                 report["flags"].append("TAMPER_WARNING: Document was modified after creation")

    except Exception as e:
        print(f"[FORENSICS] Error scanning PDF: {e}")
        report["flags"].append("ERROR: Could not verify document integrity")

    return report


def apply_forensics_penalty(base_score: int, forensics_result: dict) -> dict:
    """
    Apply a deterministic credit score penalty when pikepdf forensics detects
    document tampering (``is_suspicious == True``).

    Design decisions
    ----------------
    * **Pure function** — no side effects, no I/O, trivially unit-testable.
    * **Single application** — the penalty is applied exactly once per call;
      callers must not call this function twice for the same document.
    * **Score floor** — the adjusted score is clamped to 0 (never negative).
    * **Defensive** — every failure mode returns the original score unchanged;
      the system never crashes on bad forensics data.

    Parameters
    ----------
    base_score : int
        The AI-estimated credit score (0–100) produced by
        ``DocumentIngestionAgent.parse_financial_statement()``.
    forensics_result : dict
        The dict returned by ``run_pdf_forensics()``.  Expected shape::

            {
                "is_suspicious": bool,
                "flags": [str, ...],
                "metadata": {"creator": str, "producer": str}
            }

        All keys are treated as optional — missing or malformed values are
        handled gracefully.

    Returns
    -------
    dict
        {
            "original_score"  : int  — base_score before any penalty,
            "adjusted_score"  : int  — base_score after penalty (>= 0),
            "penalty_applied" : bool — True only when penalty was deducted,
            "penalty_points"  : int  — points deducted (0 or FORENSICS_PENALTY),
        }
    """
    # --- 1. Normalise base_score -------------------------------------------
    # Coerce to int; fall back to 0 on non-numeric input so subsequent
    # arithmetic never raises TypeError or ValueError.
    try:
        safe_score: int = max(0, int(base_score))
    except (TypeError, ValueError):
        safe_score = 0

    _no_penalty = {
        "original_score":  safe_score,
        "adjusted_score":  safe_score,
        "penalty_applied": False,
        "penalty_points":  0,
    }

    # --- 2. Guard: forensics_result must be a non-empty dict ---------------
    if not forensics_result or not isinstance(forensics_result, dict):
        # Missing or completely invalid forensics data — cannot determine
        # suspicion. Return score unchanged (safe-fail).
        return _no_penalty

    # --- 3. Extract and validate is_suspicious -----------------------------
    raw_flag = forensics_result.get("is_suspicious")

    if raw_flag is None:
        # Key is absent from the dict — treat as not suspicious.
        return _no_penalty

    try:
        is_suspicious: bool = bool(raw_flag)
    except Exception:
        # Unexpected type that bool() itself cannot handle (extremely rare).
        return _no_penalty

    # --- 4. Apply penalty (once, with floor at 0) --------------------------
    if not is_suspicious:
        return _no_penalty

    adjusted: int = max(0, safe_score - FORENSICS_PENALTY)

    return {
        "original_score":  safe_score,
        "adjusted_score":  adjusted,
        "penalty_applied": True,
        "penalty_points":  FORENSICS_PENALTY,
    }

# Lazy init — catch import/init errors
try:
    agent = DocumentIngestionAgent()
except Exception as init_err:
    print(f"[WARN] DocumentIngestionAgent init failed: {init_err}")
    agent = None

# Ensure temp directory exists
os.makedirs("temp_uploads", exist_ok=True)

# Max file size: 20 MB
MAX_FILE_SIZE = 20 * 1024 * 1024


from fastapi import BackgroundTasks, Request, Depends
from app.core.security import get_security_context, SecurityContext
from app.database.session import get_db_session
from sqlalchemy.orm import Session
from app.services.storage import get_storage_service, StorageService
from app.services.case_service import create_case_with_outbox_event
from app.services.outbox_dispatcher import OutboxDispatcher, CeleryTransportAdapter
from app.queue.celery_app import celery_app
from app.services.idempotency_service import IdempotencyService, compute_request_fingerprint, IdempotencyInProgressError, IdempotencyConflictError

def _dispatch_outbox_latency_optimization():
    """FastAPI BackgroundTask for immediate outbox dispatch (latency optimization only)."""
    try:
        from app.database.session import get_session_factory
        transport = CeleryTransportAdapter(celery_app)
        dispatcher = OutboxDispatcher(get_session_factory(), transport)
        dispatcher.dispatch_batch()
    except Exception as e:
        print(f"[BACKGROUND DISPATCH] Dispatch failed, relying on Beat sweeper: {e}")

from typing import Any

def _get_api_db_session():
    with get_db_session() as session:
        yield session

@router.post("/ingest/pdf", status_code=202)
async def ingest_pdf_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sec_ctx: SecurityContext = Depends(get_security_context),
    session: Any = Depends(_get_api_db_session),
    storage: Any = Depends(get_storage_service)
):
    """Upload a PDF document and initialize asynchronous appraisal pipeline."""

    # 1. Idempotency Check
    idempotency_key = request.headers.get("Idempotency-Key")
    idemp_service = IdempotencyService(session)
    
    request_hash = ""
    if idempotency_key:
        from app.services.idempotency_service import PayloadTooLargeError
        content = await file.read()
        await file.seek(0)
        try:
            request_hash = compute_request_fingerprint("POST", "/api/v1/documents/ingest/pdf", content)
            is_replayed, cached_response = idemp_service.process_idempotent_request(
                tenant_id=sec_ctx.tenant_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash
            )
            if is_replayed:
                return cached_response
        except IdempotencyInProgressError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except IdempotencyConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except PayloadTooLargeError as e:
            raise HTTPException(status_code=413, detail=str(e))

    # Validate filename
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF. Received: " + file.filename)

    # Sanitize filename (remove path traversal attempts)
    safe_filename = os.path.basename(file.filename).replace("..", "").replace("/", "").replace("\\", "")
    if not safe_filename:
        safe_filename = "uploaded_document.pdf"

    # Read and validate file size
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.")

    # 2. Storage Upload (Fail-Closed Boundary)
    file_uuid = uuid.uuid4().hex
    unique_filename = f"{file_uuid}_{safe_filename}"
    
    # We pass placeholders for case_id/doc_id to the storage service as they haven't been created yet.
    case_id = f"case_{uuid.uuid4().hex}"
    doc_id = f"doc_{uuid.uuid4().hex}"
    
    try:
        storage_key = storage.upload_file(
            tenant_id=sec_ctx.tenant_id,
            case_id=case_id,
            document_id=doc_id,
            filename=unique_filename,
            content=content,
            content_type="application/pdf"
        )
    except Exception as e:
        print(f"[STORAGE ERROR] Failed to upload {safe_filename}: {e}")
        raise HTTPException(status_code=500, detail="Failed to persist document to secure storage.")

    # 3. Atomic Case Creation + Outbox Event
    try:
        case = create_case_with_outbox_event(
            session=session,
            tenant_id=sec_ctx.tenant_id,
            borrower_name="Unknown",  # To be extracted by async pipeline
            total_loan_amount=0.0,
            company_type="UNKNOWN",
            deduplication_key=idempotency_key if idempotency_key else f"dedup_{case_id}",
            case_id=case_id,
            document_id=doc_id,
            storage_key=storage_key
        )
        
        # We don't manually session.commit() here, because the `get_db_session` dependency manages it.
        # But we must flush to ensure idempotency records and case records are written.
        session.flush()
        
    except Exception as e:
        print(f"[DB ERROR] Workflow transaction failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize appraisal workflow.")

    # 4. Construct HTTP 202 Response
    response_body = {
        "status": "processing",
        "message": "Document ingested successfully. Appraisal pipeline initialized.",
        "case_id": case.id,
        "job_id": case.jobs[0].id if case.jobs else None,
        "correlation_id": sec_ctx.correlation_id
    }

    if idempotency_key:
        idemp_service.store_response(
            tenant_id=sec_ctx.tenant_id,
            idempotency_key=idempotency_key,
            status_code=202,
            response_body=response_body
        )

    # Trigger Latency Optimization (wake Celery beat instantly)
    background_tasks.add_task(_dispatch_outbox_latency_optimization)

    return response_body
