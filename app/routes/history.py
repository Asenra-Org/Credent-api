# =============================================================================
# CREDENT — Appraisal History Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter
from app.database.database import get_recent_appraisals

router = APIRouter()

@router.get("/recent")
async def fetch_recent_appraisals(limit: int = 10):
    try:
        appraisals = get_recent_appraisals(limit)
        # Format for frontend consistency if needed
        # (e.g., date formats, or mapping IDs)
        return {"status": "success", "data": appraisals}
    except Exception as e:
        print(f"[ROUTE /history/recent] Error: {e}")
        return {"status": "error", "message": str(e), "data": []}
