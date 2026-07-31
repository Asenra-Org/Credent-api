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
from app.database.database import save_appraisal

@router.post("/ingest/pdf")
async def ingest_pdf_document(file: UploadFile = File(...), institution_id: str = "DEFAULT"):
    """Upload a PDF document, trigger full multi-agent credit appraisal, and persist record."""

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

        # 1. Run Integrity Scan (Forensics)
        forensics = run_pdf_forensics(temp_file_path)

        # 2. Trigger Full Orchestrator Multi-Agent Appraisal Pipeline (ADR-006)
        coordinator = AgentCoordinator()
        appraisal_result = await coordinator.run_appraisal({
            "file_path": temp_file_path,
            "institution_id": institution_id
        })

        if isinstance(appraisal_result, dict):
            appraisal_result["forensics"] = forensics
            
            # 3. Save appraisal results to Supabase (Primary) and SQLite (Fallback)
            try:
                save_appraisal({
                    "company_id": f"COMP_{int(datetime.now().timestamp())}",
                    "company_name": appraisal_result.get("individual_agent_outputs", {}).get("ingestion", {}).get("company_name", "Unknown Entity"),
                    "sector": appraisal_result.get("individual_agent_outputs", {}).get("sector_context", {}).get("sector", "N/A"),
                    "revenue": appraisal_result.get("individual_agent_outputs", {}).get("financial_health", {}).get("metrics", {}).get("revenue", 0.0),
                    "debt": appraisal_result.get("individual_agent_outputs", {}).get("financial_health", {}).get("metrics", {}).get("total_debt", 0.0),
                    "base_score": appraisal_result.get("individual_agent_outputs", {}).get("financial_health", {}).get("financial_health_score", 50),
                    "adjusted_score": appraisal_result.get("individual_agent_outputs", {}).get("financial_health", {}).get("financial_health_score", 50),
                    "decision": appraisal_result.get("combined_decision", {}).get("decision", "PENDING"),
                    "recommended_loan_amount": appraisal_result.get("combined_decision", {}).get("recommended_loan_amount", "0"),
                    "recommended_interest_rate": appraisal_result.get("combined_decision", {}).get("recommended_interest_rate", "N/A"),
                    "decision_rationale": appraisal_result.get("combined_decision", {}).get("decision_rationale", ""),
                    "cam_report": appraisal_result.get("combined_decision", {}),
                    "web_research": {},
                    "integrity_flags": appraisal_result.get("individual_agent_outputs", {}).get("integrity_check", {}),
                    "raw_document_data": appraisal_result.get("individual_agent_outputs", {}).get("ingestion", {}),
                    "financial_ratios": appraisal_result.get("individual_agent_outputs", {}).get("financial_health", {}).get("ratios", {}),
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
