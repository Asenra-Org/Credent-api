# =============================================================================
# CREDENT — Integrity Analysis Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent

router = APIRouter()

try:
    integrity_agent = IntegrityVerificationAgent()
except Exception as init_err:
    print(f"[WARN] IntegrityVerificationAgent init failed: {init_err}")
    integrity_agent = None


class IntegrityCheckRequest(BaseModel):
    gst_data: List[Dict[str, Any]] = Field(default_factory=list)
    bank_data: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/integrity-check")
async def check_data_integrity(raw_request: Request):
    """Cross-validate GST returns against Bank Statements to detect fraud."""
    try:
        body = await raw_request.json()
        
        # Parse request with defaults
        request = IntegrityCheckRequest(**body)
        
        if integrity_agent is None:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "Integrity verification service not available."
            }
        
        # Validate we have data to work with
        if not request.gst_data and not request.bank_data:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No GST or bank data provided."
            }
        
        results = await integrity_agent.cross_validate(request.gst_data, request.bank_data)
        return results
        
    except Exception as e:
        print(f"[ROUTE /integrity-check] Error: {e}")
        return {
            "status": "completed",
            "flags_detected": 0,
            "flags": [],
            "warning": f"Integrity check encountered an error: {str(e)}"
        }