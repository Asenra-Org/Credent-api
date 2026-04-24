from fastapi import APIRouter, Request
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
        return {
            "status": "success",
            "data": {
                "company_news": [f"Research failed: {str(e)}"],
                "sector_headwinds": [],
                "litigation_signals": []
            }
        }


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
        # Try to extract base_score from original body for fallback
        try:
            fallback_score = max(0, min(100, int(body.get("base_score", 50))))
        except Exception:
            fallback_score = 50
            
        return {
            "status": "success",
            "data": {
                "original_score": fallback_score,
                "adjusted_score": fallback_score,
                "adjustment_rationale": f"Score adjustment failed: {str(e)}. Original score returned.",
                "critical_flags": []
            }
        }