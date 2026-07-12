# =============================================================================
# CREDENT — Management Quality Agent (Promoter & Governance Analysis)
# A product of Asenra
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import json
import os
import re
import logging
from typing import Any

from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
UNDETERMINED = "Undetermined"
UNKNOWN_COMPANY = "Unknown Company"


class ManagementQualityAgent:
    """Evaluates the quality and credibility of the management team."""

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            logger.warning("GROQ_API_KEY not set. Falling back to dummy key.")

        self.llm = ChatGroq(
            model="llama-3.1-8b-instant",
            temperature=0,
            api_key=api_key or "dummy",
            model_kwargs={"response_format": {"type": "json_object"}}
        )

    async def analyze(self, entity_data: dict[str, Any]) -> dict[str, Any]:
        """Run management quality assessment via agnostic orchestration."""
        company_name, promoters = self._extract_and_normalize_input(entity_data)

        try:
            raw_history = await self.check_promoter_history(promoters)
        except Exception:
            logger.exception("LLM promoter history check failed due to an unexpected error.")
            return self._build_fallback_response(company_name)

        validated_history = self._validate_llm_response(raw_history)

        try:
            return self._map_to_management_response(company_name, promoters, validated_history)
        except Exception:
            logger.exception("Failed to map management quality response to API schema.")
            return self._build_fallback_response(company_name)

    def _extract_and_normalize_input(self, entity_data: dict[str, Any]) -> tuple[str, list[str]]:
        """Defensively parse the untrusted entity_data dictionary."""
        if not isinstance(entity_data, dict):
            return UNKNOWN_COMPANY, []

        company_name = str(entity_data.get("company_name", UNKNOWN_COMPANY))
        promoters = entity_data.get("promoter_ids", [])

        if not isinstance(promoters, list) or len(promoters) == 0:
            promoters = [company_name]

        sanitized_promoters = [str(p) for p in promoters if p is not None]
        return company_name, sanitized_promoters

    def _validate_llm_response(self, history: dict[str, Any]) -> dict[str, Any]:
        """Treat LLM response as untrusted input; rigorously enforce types and lists."""
        if not isinstance(history, dict):
            return {
                "past_defaults": False,
                "regulatory_actions": []
            }

        # Enforce boolean evaluation defensively
        raw_defaults = history.get("past_defaults")
        past_defaults = raw_defaults if isinstance(raw_defaults, bool) else False

        # Enforce list type and sanitize elements
        raw_actions = history.get("regulatory_actions")
        if not isinstance(raw_actions, list):
            raw_actions = []

        regulatory_actions = [str(action) for action in raw_actions if action is not None]

        return {
            "past_defaults": past_defaults,
            "regulatory_actions": regulatory_actions
        }

    def _map_to_management_response(self, company_name: str, promoters: list[str], validated_history: dict[str, Any]) -> dict[str, Any]:
        """Strictly map validated intelligence into the Pydantic API response schema."""
        risk_flags = list(validated_history["regulatory_actions"])

        if validated_history["past_defaults"]:
            risk_flags.append("PAST_DEFAULTS_DETECTED")

        # We do not invent business rules. If discrete data is unavailable, we use project-safe placeholders.
        return {
            "status": STATUS_SUCCESS,
            "company_name": company_name,
            "management_score": 0.0,
            "risk_level": UNDETERMINED,
            "promoter_analysis": [
                {
                    "name": p,
                    "experience_years": 0,
                    "risk_flags": risk_flags,
                    "verdict": UNDETERMINED
                } for p in promoters
            ],
            "governance_assessment": {
                "board_independence": UNDETERMINED,
                "regulatory_compliance": UNDETERMINED,
                "risk_level": UNDETERMINED
            }
        }

    def _build_fallback_response(self, company_name: str) -> dict[str, Any]:
        """Construct a safe, fail-closed payload formatted strictly to API schemas."""
        # Unmapped extra fields (like 'warnings') have been stripped to strictly satisfy the schema.
        return {
            "status": STATUS_ERROR,
            "company_name": company_name,
            "management_score": 0.0,
            "risk_level": UNDETERMINED,
            "promoter_analysis": [],
            "governance_assessment": {
                "board_independence": UNDETERMINED,
                "regulatory_compliance": UNDETERMINED,
                "risk_level": UNDETERMINED
            }
        }

    async def check_promoter_history(
        self,
        promoter_ids: list[str],
    ) -> dict:
        """Check promoter past ventures, defaults, and regulatory actions."""
        # Sanitize promoter inputs to prevent prompt injection
        sanitized_promoters = []
        for pid in promoter_ids:
            if not isinstance(pid, str):
                continue
            # Retain only safe alphanumeric, space, comma, hyphens, and dots.
            clean_pid = re.sub(r"[^\w\s,\-\.]", "", pid).strip()
            if clean_pid:
                # Truncate to prevent buffer overflow/token spending injection attacks
                sanitized_promoters.append(clean_pid[:100])

        if not sanitized_promoters:
            return {
                "director_cibil_scores": [],
                "past_defaults": False,
                "regulatory_actions": [],
                "past_ventures": [],
                "warnings": ["No valid promoter IDs provided for verification."],
            }

        prompt = f"""
        You are a Senior Indian Credit Risk Officer.

        Analyze the provided promoter information.

        Extract:
        - Director CIBIL scores
        - Past loan defaults
        - Regulatory actions
        - Past ventures

        Return only valid JSON in this format:

        {{
            "director_cibil_scores": [],
            "past_defaults": false,
            "regulatory_actions": [],
            "past_ventures": [],
            "warnings": []
        }}

        If a past default is found:
        - Set past_defaults to true.
        - Add a clear warning to the warnings list.

        If information is unavailable, return empty lists or null.

        Promoters:
        {sanitized_promoters}
        """

        try:
            result = await self.llm.ainvoke(prompt)
        except Exception as e:
            logger.error(f"Error invoking Groq LLM: {e}", exc_info=True)
            return {
                "director_cibil_scores": [],
                "past_defaults": False,
                "regulatory_actions": [],
                "past_ventures": [],
                "warnings": ["Unable to parse promoter history response due to service outage."],
            }

        try:
            content = result.content if hasattr(result, "content") else result
            content = content.strip()

            # Extract JSON block from markdown ```json ``` wrapper if present
            markdown_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
            if markdown_match:
                content = markdown_match.group(1).strip()
            else:
                # Extract first JSON-like dictionary block { ... } if no markdown wrapper
                curly_match = re.search(r"(\{.*\})", content, re.DOTALL)
                if curly_match:
                    content = curly_match.group(1).strip()

            promoter_history = json.loads(content)
        except (json.JSONDecodeError, TypeError) as parse_err:
            logger.error(f"Failed to parse LLM response JSON: {parse_err}", exc_info=True)
            return {
                "director_cibil_scores": [],
                "past_defaults": False,
                "regulatory_actions": [],
                "past_ventures": [],
                "warnings": ["Unable to parse promoter history response due to formatting issue."],
            }

        if promoter_history.get("past_defaults"):
            warning = "Past default found in promoter history."
            logger.warning(warning)

            warnings = promoter_history.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(warning)

        return promoter_history
