# =============================================================================
# CREDENT — Institutional Policy Management Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from app.database.database import get_policy, save_policy
from app.security.dependencies import get_current_tenant, require_role, get_current_user_and_session

router = APIRouter()

class PolicyRequest(BaseModel):
    current_ratio_safe: Optional[float] = Field(default=1.2, ge=0.0)
    current_ratio_min: Optional[float] = Field(default=1.0, ge=0.0)
    dscr_safe: Optional[float] = Field(default=1.25, ge=0.0)
    dscr_min: Optional[float] = Field(default=1.0, ge=0.0)
    de_high: Optional[float] = Field(default=2.0, ge=0.0)
    auto_approve_cutoff: float = Field(default=60.0, ge=0.0, le=100.0)
    auto_reject_cutoff: float = Field(default=40.0, ge=0.0, le=100.0)
    penalty_weights: Dict[str, float] = Field(default_factory=lambda: {
        "integrity_mismatch": 15.0,
        "promoter_flags": 10.0
    })

@router.get("/policies/{institution_id}")
@router.get("/admin/policies")
@router.get("/admin/policies/{institution_id}")
async def get_institution_policy(
    institution_id: str = "DEFAULT",
    tenant_id: str = Depends(get_current_tenant),
    user_context: dict = Depends(get_current_user_and_session)
):
    """Retrieve active risk policy for a specific lending institution or default admin policy."""
    # Tenant Isolation Foundation: Do not allow cross-tenant reading
    if institution_id != "DEFAULT" and institution_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context mismatch")

    policy = get_policy(institution_id)
    if not policy:
        if institution_id in ["DEFAULT", "DEFAULT_INSTITUTION"]:
            return {
                "institution_id": "DEFAULT",
                "current_ratio_safe": 1.2,
                "current_ratio_min": 1.0,
                "dscr_safe": 1.25,
                "dscr_min": 1.0,
                "de_high": 2.0,
                "auto_approve_cutoff": 60.0,
                "auto_reject_cutoff": 40.0,
                "penalty_weights": {
                    "integrity_mismatch": 15.0,
                    "promoter_flags": 10.0
                }
            }
        raise HTTPException(status_code=404, detail=f"Policy not found for institution: {institution_id}")
    return policy

@router.put("/policies/{institution_id}")
@router.put("/admin/policies")
@router.put("/admin/policies/{institution_id}")
async def update_institution_policy(
    request: PolicyRequest,
    institution_id: str = "DEFAULT",
    tenant_id: str = Depends(get_current_tenant),
    role: str = Depends(require_role(["ORG_ADMIN", "SUPER_ADMIN"])),
    user_context: dict = Depends(get_current_user_and_session)
):
    """Create or update risk policy configuration for an institution or default admin policy."""
    # Tenant Isolation Foundation: Do not allow cross-tenant mutation
    if institution_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context mismatch")

    if request.auto_approve_cutoff <= request.auto_reject_cutoff:
        raise HTTPException(status_code=400, detail="Auto-approve cutoff must be strictly greater than auto-reject cutoff.")

    policy_data = {
        "institution_id": institution_id,
        "current_ratio_safe": request.current_ratio_safe,
        "current_ratio_min": request.current_ratio_min,
        "dscr_safe": request.dscr_safe,
        "dscr_min": request.dscr_min,
        "de_high": request.de_high,
        "auto_approve_cutoff": request.auto_approve_cutoff,
        "auto_reject_cutoff": request.auto_reject_cutoff,
        "penalty_weights": request.penalty_weights
    }

    from app.database.database import get_app_connection, get_policy
    from app.security.audit_service import create_audit_event
    import json

    # Get previous state for audit log
    previous_state = get_policy(institution_id)

    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute('''INSERT OR REPLACE INTO institution_policies
            (institution_id, current_ratio_safe, current_ratio_min, dscr_safe, dscr_min, de_high, auto_approve_cutoff, auto_reject_cutoff, penalty_weights)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                institution_id,
                request.current_ratio_safe,
                request.current_ratio_min,
                request.dscr_safe,
                request.dscr_min,
                request.de_high,
                request.auto_approve_cutoff,
                request.auto_reject_cutoff,
                json.dumps(request.penalty_weights)
            ))

        create_audit_event(
            conn=conn,
            tenant_id=tenant_id,
            user_id=user_context["user_id"],
            action="POLICY_UPDATED",
            resource_type="policy",
            resource_id=institution_id,
            previous_state=previous_state,
            new_state=policy_data
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to save institution policy with audit log.")
    finally:
        conn.close()

    return {
        "status": "success",
        "message": f"Policy updated successfully for institution: {institution_id}",
        "policy": policy_data
    }
