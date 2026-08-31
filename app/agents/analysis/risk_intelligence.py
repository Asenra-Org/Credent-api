# =============================================================================
# CREDENT — Risk Intelligence Agent (Qualitative Score Adjustment)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import re
from app.core.llm import ChatGroqWithFallback as ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

class AdjustedRiskScore(BaseModel):
    original_score: int = Field(description="The base score before qualitative adjustments (out of 100)")
    adjusted_score: int = Field(description="The new score after factoring in officer notes (out of 100)")
    adjustment_rationale: str = Field(description="Explanation of exactly why the score was penalized or boosted")
    critical_flags: List[str] = Field(default_factory=list, description="Any deal-breaking red flags raised by the field notes")

class RiskIntelligenceAgent:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            print("[WARN] GROQ_API_KEY not set. Risk agent will use passthrough defaults.")
        
        self.llm = ChatGroq(
            model=os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b"),
            temperature=0, max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            api_key=api_key or "dummy"
        )
        # Bypassed structured output to prevent token looping on Sarvam
        self.structured_llm = None

    def _extract_json_from_text(self, text: str) -> dict:
        from json_repair import repair_json
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try: return json.loads(json_match.group())
            except: 
                try: return json.loads(repair_json(json_match.group()))
                except: pass
        try: return json.loads(repair_json(text))
        except: raise ValueError("No JSON found in response")

    async def adjust_risk_with_insights(self, base_score: int, qualitative_notes: str) -> dict:
        """Adjust the quantitative risk score using qualitative human insights."""
        
        # Validate & sanitize inputs
        try:
            base_score = max(0, min(100, int(base_score)))
        except (ValueError, TypeError):
            base_score = 50
        
        if not qualitative_notes or not str(qualitative_notes).strip():
            # No notes to adjust with — return base score unchanged
            return {
                "original_score": base_score,
                "adjusted_score": base_score,
                "adjustment_rationale": "No qualitative notes provided. Score unchanged.",
                "critical_flags": []
            }
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a Chief Credit Officer. Your job is to adjust a baseline credit score (0-100) based on qualitative field notes from a credit manager. 
            - Penalize heavily (minus 10-30 points) for severe operational issues (e.g., low capacity, strikes, unrecorded debt).
            - Boost slightly (plus 5-10 points) for strong management actions or positive field observations.
            - If a note mentions fraud or severe legal trouble, flag it as a critical deal-breaker.
            - The adjusted score MUST be between 0 and 100."""),
            ("user", "Base Score: {base_score}\n\nField Officer Notes: {qualitative_notes}")
        ])

        invoke_params = {
            "base_score": base_score,
            "qualitative_notes": str(qualitative_notes)[:5000]  # Limit input
        }

        # Attempt 1: Structured output
        if self.structured_llm:
            try:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke(invoke_params)
                output = result.model_dump()
                # Clamp adjusted score
                output["adjusted_score"] = max(0, min(100, output["adjusted_score"]))
                return output
            except Exception as e:
                print(f"[RISK] Structured output failed: {e}")

        # Attempt 2: Raw LLM + JSON parse
        try:
            chain = prompt | self.llm
            raw_result = await chain.ainvoke(invoke_params)
            raw_text = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)
            parsed = self._extract_json_from_text(raw_text)
            
            # Fill defaults
            parsed.setdefault("original_score", base_score)
            parsed.setdefault("adjusted_score", base_score)
            parsed.setdefault("adjustment_rationale", "Score adjustment via AI analysis.")
            parsed.setdefault("critical_flags", [])
            
            # Clamp
            try:
                parsed["adjusted_score"] = max(0, min(100, int(parsed["adjusted_score"])))
            except (ValueError, TypeError):
                parsed["adjusted_score"] = base_score
            
            return parsed
        except Exception as e2:
            print(f"[RISK] Raw fallback failed: {e2}")

        # Attempt 3: Return unchanged score
        print("[RISK] All AI methods failed. Returning base score unchanged.")
        return {
            "original_score": base_score,
            "adjusted_score": base_score,
            "adjustment_rationale": "AI analysis unavailable. Score returned unchanged. Manual review recommended.",
            # [P1-5] Structured failure marker. The score below is the UNCHANGED input,
            # not an assessment, so the boundary validator must be able to tell that no
            # risk analysis actually happened rather than treating it as a real result.
            "agent_status": "DEGRADED",
            "error_code": "MODEL_UNAVAILABLE",
            "risk_analysis_degraded": True,
            "retryable": True,
            "critical_flags": []
        }

