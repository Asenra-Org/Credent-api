# =============================================================================
# CREDENT — Sector Context Agent (RBI Policy & Macro Analysis)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import re
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

# Default fallback responses — used only if every AI attempt fails
DEFAULT_SECTOR_OUTLOOK = {
    "outlook": "Stable",
    "growth_rate_projected": "Unavailable",
    "risk_score": 5,
    "risk_level": "Medium",
    "risk_factors": ["Unable to retrieve sector data. Manual research recommended."]
}

DEFAULT_RBI_POLICIES = [
    {
        "circular_ref": "N/A",
        "summary": "Unable to retrieve RBI circular data. Manual research recommended.",
        "impact": "Neutral"
    }
]


# --- Output schemas (used for structured LLM output) ---

class SectorOutlookReport(BaseModel):
    outlook: str = Field(description="Overall sector outlook: Positive, Stable, or Negative")
    growth_rate_projected: str = Field(description="Projected annual growth rate as a percentage string, e.g. '7.2%'")
    risk_score: int = Field(description="Macro risk score for the sector from 1 (very low risk) to 10 (very high risk)")
    risk_factors: List[str] = Field(default_factory=list, description="Key headwinds or macro risks currently facing this sector")


class RbiPolicyItem(BaseModel):
    circular_ref: str = Field(description="Reference number of the RBI circular, e.g. 'RBI/2026-27/45'")
    summary: str = Field(description="Key highlight or summary of the circular")
    impact: str = Field(description="Impact on the borrower's business: Favorable, Neutral, or Unfavorable")


class RbiPolicyList(BaseModel):
    policies: List[RbiPolicyItem] = Field(default_factory=list, description="Relevant RBI circulars for the sector")


class SectorContextAgent:
    """Provides sector-level context and macro-economic insights."""

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            print("[WARN] GROQ_API_KEY not set. Sector agent will use passthrough defaults.")

        self.llm = ChatGroq(
            model="llama-3.1-8b-instant",
            temperature=0,
            api_key=api_key or "dummy"
        )

        try:
            self.structured_llm_outlook = self.llm.with_structured_output(SectorOutlookReport, method="json_mode")
        except Exception as e:
            print(f"[WARN] Structured output init failed (outlook): {e}")
            self.structured_llm_outlook = None

        try:
            self.structured_llm_rbi = self.llm.with_structured_output(RbiPolicyList, method="json_mode")
        except Exception as e:
            print(f"[WARN] Structured output init failed (rbi): {e}")
            self.structured_llm_rbi = None

    def _extract_json_from_text(self, text: str) -> dict:
        """Try to extract JSON from raw LLM text response."""
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError("No JSON found in response")

    @staticmethod
    def _risk_level_from_score(score: int) -> str:
        """Map a 1-10 numeric risk score to a Low/Medium/High bucket."""
        if score <= 3:
            return "Low"
        if score <= 7:
            return "Medium"
        return "High"

    async def get_sector_outlook(self, sector: str) -> dict:
        """Get current macroeconomic outlook and risk rating for a given sector."""

        if not sector or not sector.strip():
            print("[SECTOR] No sector provided, returning defaults.")
            result = DEFAULT_SECTOR_OUTLOOK.copy()
            result["sector"] = "Unknown Sector"
            return result

        sector = sector.strip()

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a macroeconomic credit risk analyst at a lending institution.
            Given an industry sector, classify its current headwinds (risks) and evaluate its
            overall outlook. Reference concrete, realistic macro factors: regulation, input/commodity
            price trends, demand cycles, competitive intensity, trade policy, interest rates, and
            technology disruption in the Indian market context.

            Assign a macro risk score from 1 (very low risk, stable/growing sector) to 10
            (very high risk, sector in severe distress or highly volatile).

            You MUST output ONLY valid JSON that EXACTLY matches this schema:
            {{
                "outlook": "Positive" | "Stable" | "Negative",
                "growth_rate_projected": "e.g. 7.2%",
                "risk_score": <int 1-10>,
                "risk_factors": ["list of strings", "specific headwinds"]
            }}"""),
            ("user", "Sector: {sector}")
        ])

        invoke_params = {"sector": sector}

        # Attempt 1: Structured output
        if self.structured_llm_outlook:
            try:
                chain = prompt | self.structured_llm_outlook
                result = await chain.ainvoke(invoke_params)
                output = result.model_dump()
                output["risk_score"] = max(1, min(10, int(output.get("risk_score", 5))))
                output["risk_level"] = self._risk_level_from_score(output["risk_score"])
                output["sector"] = sector
                return output
            except Exception as e:
                print(f"[SECTOR] Structured output failed: {e}")

        # Attempt 2: Raw LLM + JSON parse
        try:
            chain = prompt | self.llm
            raw_result = await chain.ainvoke(invoke_params)
            raw_text = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)
            parsed = self._extract_json_from_text(raw_text)

            for key, default_val in DEFAULT_SECTOR_OUTLOOK.items():
                parsed.setdefault(key, default_val)

            try:
                parsed["risk_score"] = max(1, min(10, int(parsed["risk_score"])))
            except (ValueError, TypeError):
                parsed["risk_score"] = 5

            parsed["risk_level"] = self._risk_level_from_score(parsed["risk_score"])
            if not isinstance(parsed.get("risk_factors"), list):
                parsed["risk_factors"] = [str(parsed.get("risk_factors", ""))]
            parsed["sector"] = sector
            return parsed
        except Exception as e2:
            print(f"[SECTOR] Raw fallback failed: {e2}")

        # Attempt 3: Return safe defaults
        print("[SECTOR] All AI methods failed. Returning default outlook.")
        result = DEFAULT_SECTOR_OUTLOOK.copy()
        result["sector"] = sector
        return result

    async def check_rbi_policies(self, sector: str) -> list[dict]:
        """Check relevant RBI circulars and policy changes affecting a given sector."""

        if not sector or not sector.strip():
            print("[SECTOR] No sector provided for RBI check, returning defaults.")
            return DEFAULT_RBI_POLICIES.copy()

        sector = sector.strip()

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a regulatory compliance analyst specializing in RBI (Reserve Bank
            of India) circulars affecting Indian lending and industry sectors. Given a sector, list
            plausible, realistic RBI circulars or policy directions relevant to lenders assessing
            borrowers in that sector (refinancing schemes, priority sector lending norms, sector-specific
            exposure limits, etc.).

            You MUST output ONLY valid JSON that EXACTLY matches this schema:
            {{
                "policies": [
                    {{
                        "circular_ref": "e.g. RBI/2026-27/45",
                        "summary": "short summary of the circular",
                        "impact": "Favorable" | "Neutral" | "Unfavorable"
                    }}
                ]
            }}"""),
            ("user", "Sector: {sector}")
        ])

        invoke_params = {"sector": sector}

        # Attempt 1: Structured output
        if self.structured_llm_rbi:
            try:
                chain = prompt | self.structured_llm_rbi
                result = await chain.ainvoke(invoke_params)
                policies = result.model_dump().get("policies", [])
                return policies if policies else DEFAULT_RBI_POLICIES.copy()
            except Exception as e:
                print(f"[SECTOR] Structured RBI output failed: {e}")

        # Attempt 2: Raw LLM + JSON parse
        try:
            chain = prompt | self.llm
            raw_result = await chain.ainvoke(invoke_params)
            raw_text = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)
            parsed = self._extract_json_from_text(raw_text)
            policies = parsed.get("policies", [])
            if not isinstance(policies, list) or not policies:
                return DEFAULT_RBI_POLICIES.copy()
            return policies
        except Exception as e2:
            print(f"[SECTOR] Raw RBI fallback failed: {e2}")

        # Attempt 3: Return safe defaults
        print("[SECTOR] All RBI policy checks failed. Returning defaults.")
        return DEFAULT_RBI_POLICIES.copy()