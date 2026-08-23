# =============================================================================
# CREDENT — Sector Context Agent (RBI Policy & Macro Analysis)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import re
import csv
from pathlib import Path
from collections import defaultdict
from typing import Any, Callable, List, Optional

from app.core.llm import ChatGroqWithFallback as ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Deterministic fallback data — used only if every AI attempt fails.
# Kept as plain dicts (not model instances) so callers get the same shape
# regardless of which tier of the fallback chain produced the response.
# -----------------------------------------------------------------------------
DEFAULT_SECTOR_OUTLOOK = {
    "outlook": "Stable",
    "growth_rate_projected": "Unavailable",
    "risk_score": 5,
    "risk_level": "Medium",
    "risk_factors": ["Unable to retrieve sector data. Manual research recommended."],
}

DEFAULT_RBI_POLICIES = [
    {
        "circular_ref": "N/A",
        "summary": "Unable to retrieve RBI circular information.",
        "impact": "Unknown",
    }
]

_VALID_OUTLOOKS = {"Positive", "Stable", "Negative"}


# --- Output schemas (used for structured LLM output) ------------------------

class SectorOutlookReport(BaseModel):
    outlook: str = Field(description="Overall sector outlook: Positive, Stable, or Negative")
    growth_rate_projected: str = Field(description="Projected annual growth rate as a percentage string, e.g. '7.2%'")
    risk_score: int = Field(description="Macro risk score for the sector from 1 (very low risk) to 10 (very high risk)")
    risk_factors: List[str] = Field(default_factory=list, description="Key headwinds or macro risks currently facing this sector")


class RbiPolicyList(BaseModel):
    """Sector-level circular listing schema — this agent only ever needs
    this shape; there is no borrower-specific compliance checking here.
    """
    policies: List[dict] = Field(default_factory=list, description="Relevant RBI circulars for the sector")


class SectorContextAgent:
    """Provides sector-level context and macro-economic insights.

    Every AI-backed method in this class follows the same three-tier
    fallback chain (see _run_with_fallback):
        1. Structured LLM output (pydantic-validated).
        2. Raw LLM output, JSON-parsed manually.
        3. Deterministic default data.
    This guarantees callers always get a well-shaped response, even if the
    LLM provider is unavailable or returns malformed output.
    """

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            print("[WARN] GROQ_API_KEY not set. Sector agent will use passthrough defaults.")

        self.llm = ChatGroq(
            model=os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b"),
            temperature=0, max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            api_key=api_key or "dummy",
        )

        self.structured_llm_outlook = self._build_structured_llm(SectorOutlookReport, "outlook")
        self.structured_llm_rbi_legacy = self._build_structured_llm(RbiPolicyList, "rbi-legacy")

        # Local macro headwinds database — loaded once here, never re-read per request.
        self.macro_headwinds = self._load_macro_headwinds()

    def _build_structured_llm(self, schema: type[BaseModel], label: str):
        """Wrap with_structured_output() init in a try/except so a schema
        binding failure at startup degrades to the raw-JSON fallback tier
        instead of crashing the whole agent.
        """
        try:
            return self.llm.with_structured_output(schema, method="json_mode")
        except Exception as e:
            print(f"[WARN] Structured output init failed ({label}): {e}")
            return None

    def _load_macro_headwinds(self):
        """
        Load macro headwinds from local CSV, once, at agent init.

        Stores full rows (risk_factor, severity, category) per sector so
        richer metadata is available in memory without re-reading the file.

        Returns:
            dict[str, list[dict]]
        """

        csv_path = (
            Path(__file__).resolve().parents[2]
            / "database"
            / "macro_headwinds.csv"
        )
        print("CSV Path:", csv_path)
        sector_data = defaultdict(list)

        try:
            with open(csv_path, encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)

                for row in reader:
                    sector = row["sector"].strip().lower()

                    sector_data[sector].append({
                        "risk_factor": row.get("risk_factor", "").strip(),
                        "severity": row.get("severity", "").strip(),
                        "category": row.get("category", "").strip(),
                    })

        except FileNotFoundError:
            print(f"[WARN] Macro headwinds CSV not found: {csv_path}")

        except Exception as e:
            print(f"[WARN] Failed loading macro headwinds: {e}")

        print("Loaded sectors:", list(sector_data.keys()))
        return dict(sector_data)

    def get_local_macro_headwinds(self, sector: str) -> List[str]:
        """
        Retrieve macro headwinds for a sector from local CSV.

        Returns only the risk_factor strings (backward compatible),
        even though rows are stored internally with severity/category.
        """

        if not sector:
            return []

        rows = self.macro_headwinds.get(sector.strip().lower(), [])

        return [row["risk_factor"] for row in rows if row.get("risk_factor")]

    def has_sector(self, sector: str) -> bool:
        return sector.strip().lower() in self.macro_headwinds

    @staticmethod
    def _extract_json_from_text(text: str) -> dict:
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

    @staticmethod
    def _risk_level_from_score(score: int) -> str:
        """Map a 1-10 numeric risk score to a Low/Medium/High bucket."""
        if score <= 3:
            return "Low"
        if score <= 7:
            return "Medium"
        return "High"

    async def _run_with_fallback(
        self,
        *,
        prompt: ChatPromptTemplate,
        invoke_params: dict,
        structured_llm,
        validate: Callable[[dict], Optional[Any]],
        default: Any,
        log_prefix: str,
    ):
        """Shared three-tier fallback chain used by every AI-backed method.

        Args:
            prompt: The chat prompt to send.
            invoke_params: Variables to fill into the prompt template.
            structured_llm: A with_structured_output()-wrapped LLM, or None
                if binding failed at init.
            validate: Takes a raw dict (from either tier 1 or tier 2) and
                returns a cleaned/validated result, or None if the response
                is unusable and the next tier should be tried.
            default: Deterministic value to return if both AI tiers fail.
            log_prefix: Short tag used in warning logs, e.g. "[SECTOR]".

        Returns:
            A validated result from tier 1 or 2, or a deep copy of `default`.
        """
        # Tier 1: structured output, already schema-validated by pydantic.
        if structured_llm:
            try:
                chain = prompt | structured_llm
                result = await chain.ainvoke(invoke_params)
                validated = validate(result.model_dump())
                if validated is not None:
                    return validated
            except Exception as e:
                print(f"{log_prefix} Structured output failed: {e}")

        # Tier 2: raw text output, manually parsed and validated.
        try:
            chain = prompt | self.llm
            raw_result = await chain.ainvoke(invoke_params)
            raw_text = raw_result.content if hasattr(raw_result, "content") else str(raw_result)
            parsed = self._extract_json_from_text(raw_text)
            validated = validate(parsed)
            if validated is not None:
                return validated
        except Exception as e:
            print(f"{log_prefix} Raw fallback failed: {e}")

        # Tier 3: deterministic default.
        print(f"{log_prefix} All AI methods failed. Returning default.")
        return json.loads(json.dumps(default))  # cheap deep copy, dict or list

    # -- Sector outlook -------------------------------------------------------

    @staticmethod
    def _validate_sector_outlook(data: dict) -> Optional[dict]:
        """Coerce and sanity-check a sector outlook response.

        Clamps risk_score into 1-10, derives risk_level from it, defaults
        outlook to 'Stable' if the model returns something off-schema, and
        ensures risk_factors is always a list of strings. Returns None only
        if the payload isn't a dict at all, signalling the caller to fall
        through to the next tier.
        """
        if not isinstance(data, dict):
            return None

        outlook = data.get("outlook")
        if outlook not in _VALID_OUTLOOKS:
            outlook = "Stable"

        try:
            risk_score = max(1, min(10, int(data.get("risk_score", 5))))
        except (ValueError, TypeError):
            risk_score = 5

        risk_factors = data.get("risk_factors", [])
        if not isinstance(risk_factors, list):
            risk_factors = [str(risk_factors)]

        return {
            "outlook": outlook,
            "growth_rate_projected": str(data.get("growth_rate_projected", "Unavailable")),
            "risk_score": risk_score,
            "risk_level": SectorContextAgent._risk_level_from_score(risk_score),
            "risk_factors": risk_factors,
        }

    async def get_sector_outlook(self, sector: str) -> dict:
        """Get current macroeconomic outlook and risk rating for a given sector.
        
        LLM supplies 'outlook', 'growth_rate_projected', 'risk_score', and 'risk_level'.
        'risk_factors' is strictly overridden by local CSV data via get_local_macro_headwinds().
        """
        if not sector or not sector.strip():
            print("[SECTOR] No sector provided, returning defaults.")
            return {**DEFAULT_SECTOR_OUTLOOK, "sector": "Unknown Sector"}

        clean_sector = sector.strip()

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a macroeconomic credit risk analyst at a lending institution.
            Given an industry sector, classify its current headwinds (risks) and evaluate its
            overall outlook. Reference concrete, realistic macro factors: regulation, input/commodity
            price trends, demand cycles, competitive intensity, trade policy, interest rates, and
            technology disruption in the Indian market context.

            Assign a macro risk score from 1 (very low risk, stable/growing sector) to 10
            (very high risk, sector in severe distress or highly volatile).

            Return ONLY valid JSON. No markdown, no commentary, no code fences.
            Schema:
            {{
                "outlook": "Positive" | "Stable" | "Negative",
                "growth_rate_projected": "e.g. 7.2%",
                "risk_score": <int 1-10>,
                "risk_factors": ["list of strings", "specific headwinds"]
            }}"""),
            ("user", "Sector: {sector}"),
        ])
        result = await self._run_with_fallback(
            prompt=prompt,
            invoke_params={"sector": sector},
            structured_llm=self.structured_llm_outlook,
            validate=self._validate_sector_outlook,
            default=DEFAULT_SECTOR_OUTLOOK,
            log_prefix="[SECTOR]",
        )


        # Get risk factors from local CSV
        local_headwinds = self.get_local_macro_headwinds(sector)

        # [P0-1] Headwind detail derives from borrower documents; log the count only.
        print(f"[SECTOR] resolved | headwind_count={len(local_headwinds or [])}")


        if local_headwinds:
            result["risk_factors"] = local_headwinds


        result["sector"] = sector

        return result

    # -- RBI policy compliance --------------------------------------------------

    @staticmethod
    def _validate_legacy_policies(data: dict) -> Optional[list]:
        """Validate the sector-level circular-listing response."""
        if not isinstance(data, dict):
            return None
        policies = data.get("policies")
        if not isinstance(policies, list) or not policies:
            return None
        return policies

    async def check_rbi_policies(self, sector: str) -> list:
        """Retrieve sector-level RBI circulars and regulatory guidance
        relevant to the specified industry sector.

        This agent evaluates sector-level regulatory context only. It does
        not check any borrower-specific raw text for compliance — that is
        handled elsewhere in the system.

        Args:
            sector: The industry sector to check (e.g. 'Manufacturing').

        Returns:
            A list of dicts, each with circular_ref, summary, and impact.
        """
        if not sector or not sector.strip():
            print("[SECTOR] No sector provided for RBI check, returning defaults.")
            return DEFAULT_RBI_POLICIES.copy()

        return await self._list_sector_circulars(sector.strip())

    async def _list_sector_circulars(self, sector: str) -> list:
        """Given a sector, return plausible, realistic RBI circulars,
        prudential norms, sector exposure guidelines, and lending
        regulations relevant to lenders assessing borrowers in that sector.

        This is a sector-level lookup only — no borrower-specific
        compliance checks are performed here.
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a regulatory compliance analyst specializing in RBI (Reserve Bank
            of India) circulars, prudential norms, and sector exposure guidelines affecting Indian
            lending institutions.

            Given a sector, list plausible, realistic RBI circulars and lending regulations relevant
            to lenders assessing borrowers in that sector. Cover things like:
            - relevant RBI circulars
            - prudential norms (provisioning, capital adequacy, asset classification)
            - sector-specific exposure limits and guidelines
            - other lending regulations relevant to the sector

            Do NOT perform any borrower-specific compliance checks — this is a sector-level lookup
            only, not an evaluation of any individual borrower's documents or disclosures.

            Return ONLY valid JSON. No markdown, no commentary, no code fences.
            Schema:
            {{
                "policies": [
                    {{
                        "circular_ref": "e.g. RBI/2026-27/45",
                        "summary": "short summary of the circular",
                        "impact": "Favorable" | "Neutral" | "Unfavorable"
                    }}
                ]
            }}"""),
            ("user", "Sector: {sector}"),
        ])

        return await self._run_with_fallback(
            prompt=prompt,
            invoke_params={"sector": sector},
            structured_llm=self.structured_llm_rbi_legacy,
            validate=self._validate_legacy_policies,
            default=DEFAULT_RBI_POLICIES,
            log_prefix="[SECTOR]",
        )

