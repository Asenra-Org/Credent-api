# =============================================================================
# CREDENT — CAM Generation & Loan Status Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Header, Depends
from pydantic import BaseModel, ValidationError, Field
from typing import Dict, Any, Optional
# from typing import Dict, Any
from datetime import datetime, timezone
from fastapi import Depends
from app.security.dependencies import require_role, get_current_tenant, get_current_user_and_session
from app.security.audit_service import create_audit_event
from app.database.database import get_sqlite_connection
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
    print("[OK] Cloud-Decision Engine: ACTIVE")
except Exception as db_err:
    print(f"[ERROR] CRITICAL SYNC AGENT FAILURE: {db_err}")
    save_appraisal = None
    supabase = None

class CAMRequest(BaseModel):
    extracted_pdf_data: Dict[str, Any] = Field(default_factory=dict)
    integrity_flags: Dict[str, Any] = Field(default_factory=lambda: {"flags_detected": 0, "flags": []})
    web_research: Dict[str, Any] = Field(default_factory=lambda: {"company_news": [], "sector_headwinds": [], "litigation_signals": []})
    financial_ratios: Dict[str, Any] = Field(default_factory=dict)
    final_score: float = 0
    management_score: float = 0.0
    promoter_analysis: list[Dict[str, Any]] = Field(default_factory=list)
    governance_assessment: Dict[str, Any] = Field(default_factory=dict)


class StatusUpdate(BaseModel):
    decision: str  # APPROVE, REJECT, MANUAL REVIEW
    rationale: str
    override_reason: Optional[str] = None
    is_override: bool = False

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

from app.database.database import save_appraisal

@router.patch("/update-status/{appraisal_id}", dependencies=[Depends(require_role(["Credit Manager", "Admin"]))])
async def update_loan_status(
    appraisal_id: str,
    update: StatusUpdate,
    tenant_id: str = Depends(get_current_tenant),
    user_ctx: dict = Depends(get_current_user_and_session)
):
    """Formally Approve or Reject a loan in Supabase (Primary) and SQLite (Fallback)."""
    user_id = user_ctx["user_id"]

    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        cursor = conn.cursor()
        cursor.execute('''UPDATE appraisal_records
            SET decision = ?, decision_rationale = ?, override_reason = ?, is_override = ?
            WHERE id = ? AND institution_id = ?''', (update.decision, update.rationale, update.override_reason, 1 if update.is_override else 0, appraisal_id, tenant_id))

        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Appraisal not found or access denied.")

        create_audit_event(
            conn=conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="STATUS_UPDATED",
            resource_type="appraisal_records",
            resource_id=appraisal_id,
            new_state={
                "decision": update.decision,
                "rationale": update.rationale,
                "override_reason": update.override_reason,
                "is_override": update.is_override
            }
        )

        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to update application status across database backends.")
    finally:
        conn.close()
    status_map = {
        "APPROVE": "APPROVED",
        "REJECT": "REJECTED",
        "PENDING": "UNDER_REVIEW",
        "MANUAL": "UNDER_REVIEW"
    }
    final_status = status_map.get(update.decision, "UNDER_REVIEW")

    # Best-effort Supabase sync
    if supabase:
        try:
            supabase.table("loan_applications").update({
                "decision": update.decision,
                "status": final_status,
                "decision_rationale": update.rationale,
                "override_reason": update.override_reason,
                "is_override": update.is_override
            }).eq("id", appraisal_id).eq("institution_id", tenant_id).execute()
        except Exception:
            pass

    return {"status": "success", "message": f"Loan {update.decision} (Status: {final_status}) updated successfully."}

@router.post("/approve/{case_id}", dependencies=[Depends(require_role(["Credit Manager", "Admin"]))])
async def manager_approve_case(
    case_id: str,
    update: StatusUpdate,
    background_tasks: BackgroundTasks,
    user_ctx: dict = Depends(get_current_user_and_session)
):
    """[ASE-63] Resumes a PAUSED pipeline with an authenticated manager override."""
    manager_identity = user_ctx["user_id"]

    from app.database.database import get_case, update_case_result

    # Check if case exists and is PAUSED
    case = get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    if case.get("status") != "PAUSED":
        raise HTTPException(
            status_code=400,
            detail=f"Only PAUSED cases can be approved. Current status: {case.get('status')}"
        )

    # Inject manager decision and audit data into result_data
    result_data = case.get("result_data", {})
    if result_data is None:
        result_data = {}
    result_data["manager_decision"] = update.decision
    result_data["manager_rationale"] = update.rationale
    result_data["reviewed_by"] = manager_identity
    result_data["reviewed_at"] = datetime.now(timezone.utc).isoformat()

    # We update result_data but keep status as PAUSED, resume_appraisal_job flips it to RUNNING
    update_case_result(case_id, result_data, status="PAUSED")

    from app.services.appraisal_worker import resume_appraisal_job

    # Wrap coroutine so BackgroundTasks can run it correctly.
    background_tasks.add_task(resume_appraisal_job, case_id)

    return {
        "status": "success",
        "message": f"Case {case_id} override registered. Pipeline resumed.",
        "decision": update.decision,
        "reviewed_by": manager_identity
    }

@router.post("/generate-cam", dependencies=[Depends(require_role(["Credit Analyst", "Credit Manager", "Admin"]))])
async def generate_credit_appraisal_memo(
    raw_request: Request,
    tenant_id: str = Depends(get_current_tenant)
):
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
                    "cam_report": results,
                    "financial_ratios": request.financial_ratios or {},
                    "management_score": request.management_score,
                    "promoter_analysis": request.promoter_analysis,
                    "governance_assessment": request.governance_assessment,
                    "institution_id": tenant_id
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
