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

from app.core.llm import ChatGroqWithFallback as ChatGroq

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
            return self._build_fallback_response(company_name, fallback_reason="llm_failure")

        validated_history = self._validate_llm_response(raw_history)

        try:
            return self._map_to_management_response(company_name, promoters, validated_history)
        except Exception:
            logger.exception("Failed to map management quality response to API schema.")
            return self._build_fallback_response(company_name, fallback_reason="llm_failure")

    def _extract_and_normalize_input(self, entity_data: dict[str, Any]) -> tuple[str, list[str]]:
        """Defensively parse the untrusted entity_data dictionary."""
        if not isinstance(entity_data, dict):
            return UNKNOWN_COMPANY, []

        company_name = str(entity_data.get("company_name", UNKNOWN_COMPANY))
        promoters = entity_data.get("promoter_ids", [])

        if not isinstance(promoters, list) or len(promoters) == 0:
            promoters = []

        sanitized_promoters = [str(p) for p in promoters if p is not None]
        return company_name, sanitized_promoters

    def _validate_llm_response(self, history: dict[str, Any]) -> dict[str, Any]:
        """Treat LLM response as untrusted input; rigorously enforce types."""
        if not isinstance(history, dict) or history.get("_extraction_failed"):
            reason = history.get("fallback_reason", "llm_failure") if isinstance(history, dict) else "llm_failure"
            return {"_extraction_failed": True, "fallback_reason": reason}

        def _get_bool(key: str) -> bool:
            val = history.get(key)
            return bool(val) if isinstance(val, bool) else False

        def _get_int(key: str) -> int:
            val = history.get(key)
            if isinstance(val, int):
                return max(0, val)
            try:
                return max(0, int(val))
            except (ValueError, TypeError):
                return 0

        # Extracted variables
        wilful_default = _get_bool("wilful_default")
        fraud_misconduct = _get_bool("fraud_misconduct")
        bankruptcy_insolvency = _get_bool("bankruptcy_insolvency")
        director_disqualification = _get_bool("director_disqualification")
        historical_default_count = _get_int("historical_default_count")
        minor_regulatory_actions = _get_bool("minor_regulatory_actions")

        # Handle contradiction: wilful default requires at least one default context
        # But knockout is authoritative, so we don't necessarily need to mutate the count.

        return {
            "wilful_default": wilful_default,
            "fraud_misconduct": fraud_misconduct,
            "bankruptcy_insolvency": bankruptcy_insolvency,
            "director_disqualification": director_disqualification,
            "historical_default_count": historical_default_count,
            "minor_regulatory_actions": minor_regulatory_actions,
        }

    def calculate_management_score(self, validated_history: dict[str, Any]) -> tuple[float, bool]:
        """Calculate deterministic score and return (score, is_knockout)."""
        # Knockouts
        if (validated_history.get("wilful_default") or
            validated_history.get("fraud_misconduct") or
            validated_history.get("bankruptcy_insolvency") or
            validated_history.get("director_disqualification")):
            return 0.0, True

        score = 100.0

        # Defaults (non-wilful)
        default_count = validated_history.get("historical_default_count", 0)
        if default_count > 1:
            score -= 45.0
        elif default_count == 1:
            score -= 35.0

        # Regulatory Actions
        if validated_history.get("minor_regulatory_actions"):
            score -= 15.0

        score = max(0.0, min(100.0, score))
        return score, False

    def _map_to_management_response(self, company_name: str, promoters: list[str], validated_history: dict[str, Any]) -> dict[str, Any]:
        """Strictly map validated intelligence into the Pydantic API response schema."""
        if validated_history.get("_extraction_failed"):
            reason = validated_history.get("fallback_reason", "llm_failure")
            return self._build_fallback_response(company_name, fallback_reason=reason)

        score, is_knockout = self.calculate_management_score(validated_history)

        risk_flags = []
        if validated_history.get("wilful_default"): risk_flags.append("WILFUL_DEFAULT")
        if validated_history.get("fraud_misconduct"): risk_flags.append("FRAUD_MISCONDUCT")
        if validated_history.get("bankruptcy_insolvency"): risk_flags.append("BANKRUPTCY_INSOLVENCY")
        if validated_history.get("director_disqualification"): risk_flags.append("DIRECTOR_DISQUALIFICATION")
        if validated_history.get("historical_default_count", 0) > 0: risk_flags.append("PAST_DEFAULTS_DETECTED")
        if validated_history.get("minor_regulatory_actions"): risk_flags.append("MINOR_REGULATORY_ACTIONS")

        if is_knockout or score < 50:
            risk_level = "High"
        elif score < 75:
            risk_level = "Medium"
        else:
            risk_level = "Low"

        return {
            "status": STATUS_SUCCESS,
            "company_name": company_name,
            "management_score": score,
            "risk_level": risk_level,
            "requires_manual_review": False,
            "is_knockout": is_knockout,
            "promoter_analysis": [
                {
                    "name": p,
                    "experience_years": 0,
                    "risk_flags": risk_flags,
                    "verdict": risk_level
                } for p in promoters
            ],
            "governance_assessment": {
                "board_independence": UNDETERMINED,
                "regulatory_compliance": UNDETERMINED,
                "risk_level": UNDETERMINED
            }
        }

    def _build_fallback_response(self, company_name: str, fallback_reason: str = "llm_failure") -> dict[str, Any]:
        """Construct a safe, fail-closed payload formatted strictly to API schemas."""
        return {
            "status": STATUS_ERROR,
            "company_name": company_name,
            "management_score": 0.0,
            "risk_level": "Undetermined",
            "requires_manual_review": True,
            "fallback_reason": fallback_reason,
            "is_knockout": False,
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
                "_extraction_failed": True,
                "warnings": ["No valid promoter IDs provided for verification."],
                "fallback_reason": "missing_promoter"
            }

        prompt = f"""
        You are a Senior Indian Credit Risk Officer.

        Analyze the provided promoter information.

        Extract qualitative facts to determine promoter risk.

        Return only valid JSON in this exact format:

        {{
            "wilful_default": false,
            "fraud_misconduct": false,
            "bankruptcy_insolvency": false,
            "director_disqualification": false,
            "historical_default_count": 0,
            "minor_regulatory_actions": false
        }}

        Guidelines:
        - wilful_default: set to true ONLY if there is evidence of intentional default.
        - fraud_misconduct: set to true if there is confirmed fraud or financial crime.
        - bankruptcy_insolvency: set to true if the entity or promoter faced bankruptcy.
        - director_disqualification: set to true if legally barred from directorship.
        - historical_default_count: integer representing the exact count of past ordinary defaults (NOTE: settled defaults still count).
        - minor_regulatory_actions: set to true if non-fatal regulatory infractions occurred.

        If information is unavailable, return false for booleans and 0 for count.

        Promoters:
        {sanitized_promoters}
        """

        try:
            result = await self.llm.ainvoke(prompt)
        except Exception as e:
            logger.error(f"Error invoking Groq LLM: {e}", exc_info=True)
            return {
                "_extraction_failed": True,
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
                "_extraction_failed": True,
                "warnings": ["Unable to parse promoter history response due to formatting issue."],
            }

        if promoter_history.get("past_defaults"):
            warning = "Past default found in promoter history."
            logger.warning(warning)

            warnings = promoter_history.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(warning)

        return promoter_history
