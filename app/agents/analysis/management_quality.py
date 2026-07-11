# =============================================================================
# CREDENT — Management Quality Agent (Promoter & Governance Analysis)
# A product of Asenra
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import json
import os

from langchain_groq import ChatGroq


class ManagementQualityAgent:
    """Evaluates the quality and credibility of the management team."""

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            print("[WARN] GROQ_API_KEY not set.")

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
        {promoter_ids}
        """

        if not hasattr(self.llm, "ainvoke"):
            return {
                "director_cibil_scores": [],
                "past_defaults": False,
                "regulatory_actions": [],
                "past_ventures": [],
                "warnings": ["Unable to parse promoter history response."],
            }

        try:
            result = await self.llm.ainvoke(prompt)
        except ModuleNotFoundError:
            return {
                "director_cibil_scores": [],
                "past_defaults": False,
                "regulatory_actions": [],
                "past_ventures": [],
                "warnings": ["Unable to parse promoter history response."],
            }

        try:
            content = result.content if hasattr(result, "content") else result
            promoter_history = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {
                "director_cibil_scores": [],
                "past_defaults": False,
                "regulatory_actions": [],
                "past_ventures": [],
                "warnings": ["Unable to parse promoter history response."],
            }

        if promoter_history.get("past_defaults"):
            warning = "Past default found in promoter history."

            print(f"[WARN] {warning}")

            warnings = promoter_history.setdefault("warnings", [])

            if warning not in warnings:
                warnings.append(warning)

        return promoter_history
