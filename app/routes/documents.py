# =============================================================================
# CREDENT ΓÇö Document Ingestion & PDF Forensics Route
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
# Forensics Penalty ΓÇö AI-A-W4
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
    * **Pure function** ΓÇö no side effects, no I/O, trivially unit-testable.
    * **Single application** ΓÇö the penalty is applied exactly once per call;
      callers must not call this function twice for the same document.
    * **Score floor** ΓÇö the adjusted score is clamped to 0 (never negative).
    * **Defensive** ΓÇö every failure mode returns the original score unchanged;
      the system never crashes on bad forensics data.

    Parameters
    ----------
    base_score : int
        The AI-estimated credit score (0ΓÇô100) produced by
        ``DocumentIngestionAgent.parse_financial_statement()``.
    forensics_result : dict
        The dict returned by ``run_pdf_forensics()``.  Expected shape::

            {
                "is_suspicious": bool,
                "flags": [str, ...],
                "metadata": {"creator": str, "producer": str}
            }

        All keys are treated as optional ΓÇö missing or malformed values are
        handled gracefully.

    Returns
    -------
    dict
        {
            "original_score"  : int  ΓÇö base_score before any penalty,
            "adjusted_score"  : int  ΓÇö base_score after penalty (>= 0),
            "penalty_applied" : bool ΓÇö True only when penalty was deducted,
            "penalty_points"  : int  ΓÇö points deducted (0 or FORENSICS_PENALTY),
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
        # Missing or completely invalid forensics data ΓÇö cannot determine
        # suspicion. Return score unchanged (safe-fail).
        return _no_penalty

    # --- 3. Extract and validate is_suspicious -----------------------------
    raw_flag = forensics_result.get("is_suspicious")

    if raw_flag is None:
        # Key is absent from the dict ΓÇö treat as not suspicious.
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

# Lazy init ΓÇö catch import/init errors
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

from typing import Any, List, Optional
from fastapi import BackgroundTasks, Request, Depends, UploadFile, File, HTTPException
import os
import uuid

def _get_api_db_session():
    with get_db_session() as session:
        yield session

@router.post("/ingest/pdf", status_code=202)
async def ingest_pdf_document(
    request: Request,
    background_tasks: BackgroundTasks,
    bank_statements: Optional[List[UploadFile]] = File(None),
    gst_returns: Optional[List[UploadFile]] = File(None),
    financials: Optional[List[UploadFile]] = File(None),
    sec_ctx: SecurityContext = Depends(get_security_context),
    session: Any = Depends(_get_api_db_session),
    storage: Any = Depends(get_storage_service)
):
    """Upload a PDF document and initialize asynchronous appraisal pipeline."""

    # 1. Aggregate and sort documents
    all_files = []
    if financials:
        for f in financials:
            all_files.append((f, "REQUIRED", "financials"))
    if bank_statements:
        for f in bank_statements:
            all_files.append((f, "REQUIRED", "bank_statements"))
    if gst_returns:
        for f in gst_returns:
            all_files.append((f, "OPTIONAL", "gst_returns"))
            
    if not all_files:
        raise HTTPException(status_code=400, detail="No files provided. At least one document is required.")
        
    sorted_files = sorted(all_files, key=lambda x: (x[0].filename or "", x[2]))
    
    cumulative_hash_content = b""
    valid_docs = []
    total_size = 0
    
    for f, role, dtype in sorted_files:
        if not f.filename:
            continue
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"File must be a PDF. Received: {f.filename}")
            
        content = await f.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail=f"Uploaded file {f.filename} is empty.")
            
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"File {f.filename} too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.")
            
        total_size += len(content)
        if total_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"Aggregate payload too large. Maximum total size is {MAX_FILE_SIZE // (1024*1024)}MB.")
            
        cumulative_hash_content += content
        valid_docs.append((f, role, dtype, content))
        
    if not valid_docs:
        raise HTTPException(status_code=400, detail="No valid files provided.")

    # 2. Idempotency Check
    idempotency_key = request.headers.get("Idempotency-Key")
    idemp_service = IdempotencyService(session)
    request_hash = ""
    
    if idempotency_key:
        from app.services.idempotency_service import PayloadTooLargeError
        try:
            request_hash = compute_request_fingerprint("POST", "/api/v1/documents/ingest/pdf", cumulative_hash_content)
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

    # 3. Storage Upload (Fail-Closed Boundary for REQUIRED docs)
    case_id = f"case_{uuid.uuid4().hex}"
    documents_metadata = []
    
    for f, role, dtype, content in valid_docs:
        safe_filename = os.path.basename(f.filename).replace("..", "").replace("/", "").replace("\\", "")
        if not safe_filename:
            safe_filename = "uploaded_document.pdf"

        file_uuid = uuid.uuid4().hex
        unique_filename = f"{file_uuid}_{safe_filename}"
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
            documents_metadata.append({
                "storage_key": storage_key,
                "doc_role": role,
                "document_type": dtype,
                "filename": safe_filename
            })
        except Exception as e:
            print(f"[STORAGE ERROR] Failed to upload {safe_filename}: {e}")
            if role == "REQUIRED":
                for meta in documents_metadata:
                    try:
                        storage.delete_file(tenant_id=sec_ctx.tenant_id, storage_key=meta["storage_key"])
                    except:
                        pass
                raise HTTPException(status_code=500, detail=f"Failed to persist REQUIRED document {safe_filename} to secure storage.")
            else:
                print(f"[STORAGE WARN] Skipping OPTIONAL document {safe_filename} due to storage error.")
                
    if not documents_metadata:
        raise HTTPException(status_code=500, detail="All document uploads failed.")

    # 4. Atomic Case Creation + Outbox Event
    try:
        case = create_case_with_outbox_event(
            session=session,
            tenant_id=sec_ctx.tenant_id,
            borrower_name="Unknown",  # To be extracted by async pipeline
            total_loan_amount=0.0,
            company_type="UNKNOWN",
            deduplication_key=idempotency_key if idempotency_key else f"dedup_{case_id}",
            documents_metadata=documents_metadata,
            case_id=case_id
        )
        
        # We don't manually session.commit() here, because the `get_db_session` dependency manages it.
        # But we must flush to ensure idempotency records and case records are written.
        session.flush()
        
    except Exception as e:
        print(f"[DB ERROR] Workflow transaction failed: {e}")
        for meta in documents_metadata:
            try:
                storage.delete_file(tenant_id=sec_ctx.tenant_id, storage_key=meta["storage_key"])
            except:
                pass
        raise HTTPException(status_code=500, detail="Failed to initialize appraisal workflow.")

    # 5. Construct HTTP 202 Response
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


from app.models.ase52 import Case

@router.get("/ingest/status/{case_id}")
async def get_case_status(case_id: str, session: Session = Depends(_get_api_db_session)):
    """Fetch the real-time processing status of a credit appraisal case."""
    db_record = session.query(Case).filter_by(id=case_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="Case not found.")
    
    return {
        "case_id": case_id,
        "status": db_record.status,
        "current_step": None
    }
