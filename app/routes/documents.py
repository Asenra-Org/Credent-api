# =============================================================================
# CREDENT — Document Ingestion & PDF Forensics Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import shutil
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


@router.post("/ingest/pdf")
async def ingest_pdf_document(file: UploadFile = File(...)):
    """Upload a PDF document, extract text, and run AI risk extraction."""

    # Validate agent is available
    if agent is None:
        raise HTTPException(status_code=503, detail="Document ingestion service is not available. Check server logs.")

    # Validate filename
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF. Received: " + file.filename)

    # Sanitize filename (remove path traversal attempts)
    safe_filename = os.path.basename(file.filename).replace("..", "").replace("/", "").replace("\\", "")
    if not safe_filename:
        safe_filename = "uploaded_document.pdf"

    temp_file_path = f"temp_uploads/{safe_filename}"

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

        # 1. Run Integrity Scan (Forensics) - NEW Enterprise Security Layer
        forensics = run_pdf_forensics(temp_file_path)

        # 2. Extract raw text using the agent
        extraction_result = await agent.ingest_pdf(temp_file_path)

        # Check if extraction had errors
        if extraction_result.get("error"):
            raise HTTPException(status_code=422, detail=extraction_result["error"])

        raw_text = extraction_result.get("text", "")

        if not raw_text or len(raw_text.strip()) < 10:
            raise HTTPException(status_code=422, detail="No readable text could be extracted from this PDF. Try a clearer document.")

        # 3. Run LLM parsing
        ai_analysis = await agent.parse_financial_statement(raw_text)

        appraisal_id = f"APPRAISAL_{int(datetime.now().timestamp())}"

        return {
            "status": "success",
            "appraisal_id": appraisal_id,
            "filename": safe_filename,
            "tables_found": extraction_result.get("tables_count", 0),
            "forensics": forensics,
            "ai_analysis": ai_analysis
        }

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
