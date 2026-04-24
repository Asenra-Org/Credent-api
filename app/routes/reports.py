from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ValidationError, Field
from typing import Dict, Any
import os

router = APIRouter()

# Safe agent initialization
try:
    from app.agents.orchestration.cam_generator import CAMGeneratorAgent
    cam_agent = CAMGeneratorAgent()
except Exception as init_err:
    print(f"[WARN] CAMGeneratorAgent init failed: {init_err}")
    cam_agent = None

# ABSOLUTE CLOUD-SYNC IMPORT
try:
    from app.database.database import save_appraisal
    from supabase import create_client, Client
    
    url: str = os.environ.get("SUPABASE_URL", "")
    key: str = os.environ.get("SUPABASE_KEY", "")
    supabase: Client = create_client(url, key)
    print("✅ Cloud-Decision Engine: ACTIVE")
except Exception as db_err:
    print(f"❌ CRITICAL SYNC AGENT FAILURE: {db_err}")
    save_appraisal = None
    supabase = None

class CAMRequest(BaseModel):
    extracted_pdf_data: Dict[str, Any] = Field(default_factory=dict)
    integrity_flags: Dict[str, Any] = Field(default_factory=lambda: {"flags_detected": 0, "flags": []})
    web_research: Dict[str, Any] = Field(default_factory=lambda: {"company_news": [], "sector_headwinds": [], "litigation_signals": []})
    final_score: float = 0

class StatusUpdate(BaseModel):
    decision: str  # APPROVE, REJECT, MANUAL REVIEW
    rationale: str

# Default CAM when everything fails
def _default_cam(score: int) -> dict:
    decision = "APPROVE" if score >= 60 else "REJECT"
    return {
        "five_cs": {
            "character": "Unable to assess. Manual review required.",
            "capacity": "Unable to assess. Manual review required.",
            "capital": "Unable to assess. Manual review required.",
            "collateral": "Unable to assess. Manual review required.",
            "conditions": "Unable to assess. Manual review required."
        },
        "decision": decision,
        "recommended_loan_amount": "Manual Assessment Required" if decision == "APPROVE" else "0",
        "recommended_interest_rate": "Manual Assessment Required" if decision == "APPROVE" else "N/A",
        "decision_rationale": f"AI analysis unavailable. Score: {score}/100. Decision based on threshold (60). Manual review strongly recommended."
    }

@router.patch("/update-status/{appraisal_id}")
async def update_loan_status(appraisal_id: str, update: StatusUpdate):
    """Formally Approve or Reject a loan in the Cloud Database."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Cloud database not connected")
    
    try:
        # Determine the RLS-compliant status mapping
        # CHECK (status IN ('PENDING', 'UNDER_REVIEW', 'APPROVED', 'REJECTED', 'FLAGGED'))
        status_map = {
            "APPROVE": "APPROVED",
            "REJECT": "REJECTED",
            "PENDING": "UNDER_REVIEW",
            "MANUAL": "UNDER_REVIEW"
        }
        final_status = status_map.get(update.decision, "UNDER_REVIEW")

        response = supabase.table("loan_applications") \
            .update({
                "decision": update.decision,
                "status": final_status,
                "decision_rationale": update.rationale
            }) \
            .eq("id", appraisal_id) \
            .execute()
        
        # Check if update actually happened
        if not response.data or len(response.data) == 0:
            print(f"⚠️ [DECISION WARNING] No row found with ID: {appraisal_id}")
            raise HTTPException(status_code=404, detail="Application record not found in Cloud Database.")
            
        print(f"🏛️ [DECISION] Formal decision logged: {update.decision}/{final_status} for {appraisal_id}")
        return {"status": "success", "message": f"Loan {update.decision} (Status: {final_status}) successfully."}
    except HTTPException: raise
    except Exception as e:
        print(f"❌ [DECISION ERROR] Failed to update status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-cam")
async def generate_credit_appraisal_memo(raw_request: Request):
    """Generate the final CAM and decision rationale."""
    try:
        # Parse JSON body
        try:
            body = await raw_request.json()
        except Exception as json_err:
            return {"status": "error", "message": f"Invalid JSON body: {str(json_err)}"}

        # Validate with Pydantic
        try:
            request = CAMRequest(**body)
        except ValidationError as ve:
            return {"status": "error", "message": f"Validation failed: {ve}"}

        score = max(0, min(100, int(request.final_score)))

        # Check if agent is available
        if cam_agent is None:
            return {"status": "success", "cam_report": _default_cam(score)}

        results = await cam_agent.generate_cam(
            request.extracted_pdf_data,
            request.integrity_flags,
            request.web_research,
            score
        )
        
        # Ensure critical fields exist
        results.setdefault("decision", "APPROVE" if score >= 60 else "REJECT")
        
        # Save to Cloud for Institutional Access
        if save_appraisal:
            try:
                save_appraisal({
                    "company_id": request.extracted_pdf_data.get("company_name", "Unknown").lower().replace(" ", "_"),
                    "company_name": request.extracted_pdf_data.get("company_name", "Unknown"),
                    "sector": request.extracted_pdf_data.get("sector", "Unknown"),
                    "revenue": request.extracted_pdf_data.get("total_revenue", 0),
                    "debt": request.extracted_pdf_data.get("total_debt", 0),
                    "base_score": request.extracted_pdf_data.get("base_score", 50),
                    "adjusted_score": score,
                    "decision": results.get("decision"),
                    "recommended_loan_amount": results.get("recommended_loan_amount"),
                    "recommended_interest_rate": results.get("recommended_interest_rate"),
                    "decision_rationale": results.get("decision_rationale"),
                    "raw_document_data": request.extracted_pdf_data,
                    "integrity_flags": request.integrity_flags,
                    "web_research": request.web_research,
                    "cam_report": results
                })
            except Exception as save_err:
                print(f"[WARN] Failed to save appraisal to Cloud: {save_err}")

        return {"status": "success", "cam_report": results}
        
    except Exception as e:
        print(f"[ROUTE /generate-cam] Unexpected error: {e}")
        try:
            score = max(0, min(100, int(body.get("final_score", 50))))
        except Exception:
            score = 50
        return {"status": "success", "cam_report": _default_cam(score)}
