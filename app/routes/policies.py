# =============================================================================
# CREDENT — Institutional Policy Management Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from app.database.database import get_policy, save_policy

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
async def get_institution_policy(institution_id: str = "DEFAULT"):
    """Retrieve active risk policy for a specific lending institution or default admin policy."""
    policy = get_policy(institution_id)
    if not policy:
        if institution_id in ["DEFAULT", "DEFAULT_INSTITUTION"]:
            # Return baseline default policy
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
async def update_institution_policy(request: PolicyRequest, institution_id: str = "DEFAULT"):
    """Create or update risk policy configuration for an institution or default admin policy."""
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
    
    success = save_policy(policy_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save institution policy to database backend.")
        
    return {
        "status": "success",
        "message": f"Policy updated successfully for institution: {institution_id}",
        "policy": policy_data
    }
