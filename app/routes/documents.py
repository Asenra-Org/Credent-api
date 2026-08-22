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
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, BackgroundTasks
from app.agents.input.document_ingestion import DocumentIngestionAgent
from app.agents.security.document_security import DocumentSecurityAgent

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


from app.agents.orchestration.coordinator import AgentCoordinator
from app.database.database import save_appraisal, get_case

from app.security.dependencies import require_role, get_current_tenant
from fastapi import Depends

@router.post("/ingest/pdf", dependencies=[Depends(require_role(["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN"]))])
async def ingest_pdf_document(
    file: UploadFile = File(...), 
    institution_id: str = "DEFAULT", 
    case_id: str = Form(None),
    tenant_id: str = Depends(get_current_tenant)
):
    """Upload a PDF document, trigger full multi-agent credit appraisal, and persist record."""
    if institution_id != "DEFAULT" and institution_id != tenant_id:
        raise HTTPException(status_code=403, detail="institution_id mismatch with authenticated tenant")
    
    # Force tenant_id as authoritative
    institution_id = tenant_id

    # Validate filename
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF. Received: " + file.filename)

    # Sanitize filename (remove path traversal attempts)
    safe_filename = os.path.basename(file.filename).replace("..", "").replace("/", "").replace("\\", "")
    if not safe_filename:
        safe_filename = "uploaded_document.pdf"

    file_uuid = uuid.uuid4().hex
    unique_filename = f"{file_uuid}_{safe_filename}"
    temp_file_path = os.path.join("temp_uploads", unique_filename)

    try:
        # Read and validate file size
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.")

        # Save uploaded file temporarily
        with open(temp_file_path, "wb") as buffer:
            buffer.write(content)

        # 0. ASE-55 Security Scan (Gate 1)
        security_scan = DocumentSecurityAgent.scan_file(temp_file_path)
        if not security_scan.is_safe:
            raise HTTPException(
                status_code=422,
                detail=f"Security validation failed. Flags: {', '.join(security_scan.flags)}"
            )
        security_warnings = security_scan.warnings

        # 1. Run Integrity Scan (Forensics)
        forensics = run_pdf_forensics(temp_file_path)

        # 2. Trigger Full Orchestrator Multi-Agent Appraisal Pipeline (ASE-54: stateful)
        coordinator = AgentCoordinator(ingestion_agent=agent)
        appraisal_result = await coordinator.run_appraisal_with_state({
            "file_path": temp_file_path,
            "institution_id": institution_id
        }, case_id=case_id)

        if isinstance(appraisal_result, dict):
            ingestion_data = appraisal_result.get("individual_agent_outputs", {}).get("ingestion", {})
            financial_data = appraisal_result.get("individual_agent_outputs", {}).get("financial_health", {})
            
            # Base score resolution: prefer ingestion_data.base_score, fallback to financial_health_score
            raw_base_score = ingestion_data.get("base_score") if isinstance(ingestion_data, dict) else None
            base_score = raw_base_score if raw_base_score is not None else financial_data.get("financial_health_score", 50)

            # Apply forensics penalty (AI-A-W4) from origin/main
            forensics_penalty = apply_forensics_penalty(
                base_score=base_score,
                forensics_result=forensics,
            )

            # If penalty applied or score adjusted, set adjusted_score on ingestion and financial outputs
            adjusted = forensics_penalty.get("adjusted_score", base_score)
            if isinstance(financial_data, dict):
                financial_data["financial_health_score"] = adjusted
            if isinstance(ingestion_data, dict):
                ingestion_data["base_score"] = adjusted

            # Attach backwards-compatible top-level keys for legacy callers & tests
            appraisal_result["forensics"] = forensics
            appraisal_result["forensics_penalty"] = forensics_penalty
            appraisal_result["filename"] = safe_filename
            appraisal_result["tables_found"] = ingestion_data.get("tables_count", 0)
            appraisal_result["ai_analysis"] = ingestion_data
            appraisal_result["security_warnings"] = security_warnings

            # 3. Save appraisal results to Supabase (Primary) and SQLite (Fallback)
            try:
                save_appraisal({
                    "company_id": f"COMP_{int(datetime.now().timestamp())}",
                    "company_name": ingestion_data.get("company_name", "Unknown Entity"),
                    "sector": appraisal_result.get("individual_agent_outputs", {}).get("sector_context", {}).get("sector", "N/A"),
                    "revenue": financial_data.get("metrics", {}).get("revenue", 0.0),
                    "debt": financial_data.get("metrics", {}).get("total_debt", 0.0),
                    "base_score": base_score,
                    "adjusted_score": forensics_penalty.get("adjusted_score", base_score),
                    "decision": appraisal_result.get("combined_decision", {}).get("decision", "PENDING"),
                    "recommended_loan_amount": appraisal_result.get("combined_decision", {}).get("recommended_loan_amount", "0"),
                    "recommended_interest_rate": appraisal_result.get("combined_decision", {}).get("recommended_interest_rate", "N/A"),
                    "decision_rationale": appraisal_result.get("combined_decision", {}).get("decision_rationale", ""),
                    "cam_report": appraisal_result.get("combined_decision", {}),
                    "web_research": {},
                    "integrity_flags": appraisal_result.get("individual_agent_outputs", {}).get("integrity_check", {}),
                    "raw_document_data": ingestion_data,
                    "financial_ratios": financial_data.get("ratios", {}),
                    "management_score": appraisal_result.get("individual_agent_outputs", {}).get("management_quality", {}).get("management_score", 0.0),
                    "promoter_analysis": appraisal_result.get("individual_agent_outputs", {}).get("management_quality", {}).get("promoter_analysis", []),
                    "governance_assessment": appraisal_result.get("individual_agent_outputs", {}).get("management_quality", {}).get("governance_assessment", {}),
                    "institution_id": institution_id
                })
            except Exception as save_err:
                print(f"[ROUTE /ingest/pdf] Persistence error: {save_err}")

        return appraisal_result

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        print(f"[ROUTE /ingest/pdf] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Document processing failed: {str(e)}")
    finally:
        # Always clean up temp file
        try:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        except Exception:
            pass

@router.get("/ingest/status/{case_id}", dependencies=[Depends(require_role(["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN", "VIEWER"]))])
async def get_case_status(case_id: str, tenant_id: str = Depends(get_current_tenant)):
    """Fetch the real-time processing status of a credit appraisal case."""
    db_record = get_case(case_id, tenant_id=tenant_id)
    if not db_record:
        raise HTTPException(status_code=404, detail="Case not found.")
    
    return {
        "case_id": case_id,
        "status": db_record.get("status"),
        "current_step": db_record.get("current_step"),
        "result": db_record.get("result_data") or None,
        "error": db_record.get("error_message") or None,
        "created_at": db_record.get("created_at"),
        "updated_at": db_record.get("updated_at"),
    }


# =============================================================================
# ASE-52: Batch Ingestion Endpoint — Async Queue + Supabase Storage
# =============================================================================

@router.post("/ingest/batch", dependencies=[Depends(require_role(["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN"]))])
async def ingest_batch_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    institution_id: str = Form(default="DEFAULT"),
    tenant_id: str = Depends(get_current_tenant)
):
    if institution_id != "DEFAULT" and institution_id != tenant_id:
        raise HTTPException(status_code=403, detail="institution_id mismatch with authenticated tenant")
    institution_id = tenant_id
    """
    Batch document upload endpoint (ASE-52).

    Accepts 1–10 files (PDF, PNG, JPG, XLSX). For each file:
      1. Validates size and MIME type.
      2. Uploads to Supabase Storage (encrypted at-rest, UUID-prefixed path).
      3. Creates a loan_case DB record in PENDING state.
      4. Dispatches the appraisal job asynchronously via TaskDispatcher.
         - Local dev (USE_CELERY=false): runs via FastAPI BackgroundTasks.
         - Production (USE_CELERY=true): enqueues to Celery/Redis worker.

    Returns immediately with a list of case_ids — no blocking on AI processing.
    Poll GET /ingest/status/{case_id} to track progress.
    """
    from app.database.database import create_case
    from app.services.storage_service import upload_document
    from app.services.task_dispatcher import get_dispatcher

    # --- Validation: file count ---
    MAX_FILES = 10
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES} files allowed per batch. Got {len(files)}."
        )

    # --- Allowed MIME types ---
    ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".xls"}

    dispatcher = get_dispatcher(background_tasks)
    queued_cases = []
    errors = []

    for file in files:
        try:
            # 1. Validate filename
            if not file.filename:
                errors.append({"file": "unknown", "error": "Missing filename"})
                continue

            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext not in ALLOWED_EXTENSIONS:
                errors.append({
                    "file": file.filename,
                    "error": f"Unsupported file type '{file_ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
                })
                continue

            # 2. Read and validate file size
            content = await file.read()
            if len(content) == 0:
                errors.append({"file": file.filename, "error": "File is empty"})
                continue
            if len(content) > MAX_FILE_SIZE:
                errors.append({
                    "file": file.filename,
                    "error": f"File exceeds {MAX_FILE_SIZE // (1024 * 1024)}MB limit"
                })
                continue

            # 3. Upload to Supabase Storage (AES-256 encrypted at-rest)
            # tenant_id defaults to institution_id — prepares for RLS in Week 8
            safe_filename = os.path.basename(file.filename).replace("..", "").replace("/", "").replace("\\", "")
            storage_path = upload_document(
                file_bytes=content,
                original_filename=safe_filename,
                tenant_id=institution_id.lower().replace(" ", "_")
            )

            # 4. Create loan_case DB record in PENDING state
            case_id = uuid.uuid4().hex
            create_case(
                case_id=case_id,
                input_data={
                    "original_filename": safe_filename,
                    "storage_path": storage_path,
                    "institution_id": institution_id,
                    "file_size_bytes": len(content),
                },
                institution_id=institution_id
            )

            # 5. Dispatch async appraisal job (non-blocking)
            dispatcher.dispatch(
                case_id=case_id,
                storage_path=storage_path,
                institution_id=institution_id
            )

            queued_cases.append({
                "case_id": case_id,
                "filename": safe_filename,
                "status": "QUEUED",
                "poll_url": f"/api/v1/documents/ingest/status/{case_id}"
            })

        except Exception as e:
            print(f"[ROUTE /ingest/batch] Error processing {file.filename}: {e}")
            errors.append({"file": file.filename, "error": str(e)})

    if not queued_cases and errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "All files failed validation or upload.", "errors": errors}
        )

    return {
        "queued": len(queued_cases),
        "failed": len(errors),
        "cases": queued_cases,
        "errors": errors if errors else None,
        "message": (
            f"{len(queued_cases)} file(s) queued for processing. "
            "Poll each case's poll_url for real-time status."
        )
    }
