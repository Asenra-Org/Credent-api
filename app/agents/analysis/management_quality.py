# =============================================================================
# CREDENT — Management Quality Agent (Promoter & Governance Analysis)
# A product of Asenra
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import json
import os
import re
import logging

from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)


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
        )

    async def analyze(self, entity_data: dict) -> dict:
        """Run management quality assessment."""
        raise NotImplementedError

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
