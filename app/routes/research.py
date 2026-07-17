# =============================================================================
# CREDENT — Web Research & Risk Score Adjustment Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
from app.agents.input.realtime_intelligence import RealtimeIntelligenceAgent
from app.agents.analysis.risk_intelligence import RiskIntelligenceAgent

router = APIRouter()

try:
    research_agent = RealtimeIntelligenceAgent()
except Exception as init_err:
    print(f"[WARN] RealtimeIntelligenceAgent init failed: {init_err}")
    research_agent = None

try:
    risk_agent = RiskIntelligenceAgent()
except Exception as init_err:
    print(f"[WARN] RiskIntelligenceAgent init failed: {init_err}")
    risk_agent = None


class WebResearchRequest(BaseModel):
    company_name: str = ""
    sector: str = "General"


class AdjustScoreRequest(BaseModel):
    base_score: int = 50
    qualitative_notes: str = ""


@router.post("/web-research")
async def run_secondary_research(raw_request: Request):
    """Run a live web search for company news and sector headwinds."""
    try:
        body = await raw_request.json()
        request = WebResearchRequest(**body)
        
        if not request.company_name.strip():
            return {
                "status": "success",
                "data": {
                    "company_news": ["No company name provided."],
                    "sector_headwinds": [],
                    "litigation_signals": []
                }
            }
        
        if research_agent is None:
            return {
                "status": "success",
                "data": {
                    "company_news": ["Research service not available."],
                    "sector_headwinds": ["Research service not available."],
                    "litigation_signals": []
                }
            }
        
        results = await research_agent.conduct_research(request.company_name, request.sector)
        return {"status": "success", "data": results}
        
    except Exception as e:
        print(f"[ROUTE /web-research] Error: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Web research encountered an error: {str(e)}"
            }
        )


@router.post("/adjust-score")
async def apply_qualitative_insights(raw_request: Request):
    """Adjust a credit score based on a human credit officer's field notes."""
    try:
        body = await raw_request.json()
        request = AdjustScoreRequest(**body)
        
        # Clamp base score
        base_score = max(0, min(100, request.base_score))
        
        if risk_agent is None:
            return {
                "status": "success",
                "data": {
                    "original_score": base_score,
                    "adjusted_score": base_score,
                    "adjustment_rationale": "Risk intelligence service not available. Score unchanged.",
                    "critical_flags": []
                }
            }
        
        results = await risk_agent.adjust_risk_with_insights(base_score, request.qualitative_notes)
        return {"status": "success", "data": results}
        
    except Exception as e:
        print(f"[ROUTE /adjust-score] Error: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Score adjustment encountered an error: {str(e)}"
            }
        )