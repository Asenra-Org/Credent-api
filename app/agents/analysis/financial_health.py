# =============================================================================
# CREDENT — Financial Health Agent (Ratio Analysis & Cash Flow Assessment)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

import json
import os
import re
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Financial Health Thresholds
# ---------------------------------------------------------------------------

DSCR_SAFE_THRESHOLD = 1.25
DSCR_MIN_THRESHOLD = 1.00

CURRENT_RATIO_STRONG = 2.0
CURRENT_RATIO_SAFE = 1.0

DE_RATIO_HIGH_RISK = 2.0


# ---------------------------------------------------------------------------
# Structured Extraction Model
# ---------------------------------------------------------------------------

class FinancialHealthExtraction(BaseModel):
    """Extract financial ratios from unstructured reports."""

    interest_coverage: Optional[float] = Field(
        default=None,
        description=(
            "Interest Coverage Ratio. "
            "Extract or calculate using EBIT / Interest Expense."
        ),
    )

    operating_margin: Optional[float] = Field(
        default=None,
        description=(
            "Operating Margin percentage. "
            "Extract or calculate using Operating Income / Revenue * 100."
        ),
    )


# ---------------------------------------------------------------------------
# Financial Health Agent
# ---------------------------------------------------------------------------

class FinancialHealthAgent:
    """Analyzes financial health and extracts financial indicators."""

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")

        self.llm = None
        self.structured_llm = None

        if api_key:
            try:
                self.llm = ChatGroq(
                    model="llama-3.1-8b-instant",
                    temperature=0,
                    api_key=api_key,
                )

                self.structured_llm = (
                    self.llm.with_structured_output(
                        FinancialHealthExtraction
                    )
                )

            except Exception as exc:
                print(
                    "[WARN] FinancialHealthAgent initialization failed:",
                    exc,
                )

    # -----------------------------------------------------------------------
    # Helper Functions
    # -----------------------------------------------------------------------

    def _safe_number(self, value):
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_divide(self, numerator, denominator):
        numerator = self._safe_number(numerator)
        denominator = self._safe_number(denominator)

        if numerator is None or denominator is None:
            return None

        if denominator == 0:
            return None

        return round(numerator / denominator, 4)

    def _classify_dscr(self, dscr):
        if dscr is None:
            return "Undetermined"

        if dscr >= DSCR_SAFE_THRESHOLD:
            return "Low"

        if dscr >= DSCR_MIN_THRESHOLD:
            return "Medium"

        return "High"

    def _extract_json_from_text(self, text: str) -> dict:
        """Extract JSON object from raw LLM response."""
        match = re.search(r"\{[\s\S]*\}", text)

        if not match:
            raise ValueError("No JSON found in response")

        return json.loads(match.group())

    # -----------------------------------------------------------------------
    # Financial Ratio Calculations
    # -----------------------------------------------------------------------

    async def compute_ratios(self, financial_data: dict) -> dict:
        financial_data = financial_data or {}
        notes = []

        net_operating_income = self._safe_number(
            financial_data.get("net_operating_income")
        )

        debt_service = self._safe_number(
            financial_data.get("debt_service")
        )

        current_assets = self._safe_number(
            financial_data.get("current_assets")
        )

        current_liabilities = self._safe_number(
            financial_data.get("current_liabilities")
        )

        total_debt = self._safe_number(
            financial_data.get("total_debt")
        )

        total_equity = self._safe_number(
            financial_data.get("total_equity")
        )

        inventory = self._safe_number(
            financial_data.get("inventory")
        )

        dscr = self._safe_divide(
            net_operating_income,
            debt_service,
        )

        current_ratio = self._safe_divide(
            current_assets,
            current_liabilities,
        )

        debt_to_equity = self._safe_divide(
            total_debt,
            total_equity,
        )

        if (
            current_assets is not None
            and current_liabilities not in (None, 0)
        ):
            if inventory is not None:
                quick_ratio = self._safe_divide(
                    current_assets - inventory,
                    current_liabilities,
                )
            elif current_ratio is not None:
                quick_ratio = round(
                    current_ratio * 0.8,
                    4,
                )
            else:
                quick_ratio = None
        else:
            quick_ratio = None

        # Add warning notes
        if debt_service == 0:
            notes.append(
                "Debt service is zero. DSCR could not be calculated."
            )

        if current_liabilities == 0:
            notes.append(
                "Current liabilities are zero. Current ratio could not be calculated."
            )

        if total_equity == 0:
            notes.append(
                "Total equity is zero. Debt-to-equity ratio could not be calculated."
            )

        return {
            "dscr": dscr,
            "current_ratio": current_ratio,
            "debt_to_equity": debt_to_equity,
            "quick_ratio": quick_ratio,
            "notes": notes,
        }

    # -----------------------------------------------------------------------
    # Cash Flow Analysis
    # -----------------------------------------------------------------------

    async def assess_cash_flow(self, financial_data: dict) -> dict:
        financial_data = financial_data or {}
        notes = []

        operating_cash_flow = self._safe_number(
            financial_data.get("operating_cash_flow")
        )

        free_cash_flow = self._safe_number(
            financial_data.get("free_cash_flow")
        )

        inflows = financial_data.get(
            "historical_inflows",
            [],
        )

        if not isinstance(inflows, list):
            inflows = []

        valid_inflows = []

        for value in inflows:
            numeric = self._safe_number(value)

            if numeric is None:
                notes.append(
                    "Skipped non-numeric historical inflow."
                )
                continue

            valid_inflows.append(numeric)

        periods_analyzed = len(valid_inflows)

        if periods_analyzed == 0:
            trend = "Insufficient Data"
        elif periods_analyzed == 1:
            trend = (
                "Positive"
                if valid_inflows[0] > 0
                else "Declining"
            )
        else:
            positive = sum(
                1 for value in valid_inflows if value > 0
            )

            ratio = positive / periods_analyzed

            if ratio >= 0.70:
                if valid_inflows[-1] >= valid_inflows[0]:
                    trend = "Positive"
                else:
                    trend = "Stable"
            elif ratio >= 0.50:
                trend = "Stable"
            else:
                trend = "Declining"

        is_adequate = bool(
            operating_cash_flow is not None
            and operating_cash_flow > 0
            and free_cash_flow is not None
            and free_cash_flow > 0
            and trend in ("Positive", "Stable")
        )

        if (
            operating_cash_flow is not None
            and operating_cash_flow > 0
            and free_cash_flow is not None
            and free_cash_flow > 0
            and trend == "Positive"
        ):
            status = "Strong"
        elif (
            operating_cash_flow is None
            or operating_cash_flow <= 0
            or trend == "Declining"
        ):
            status = "Weak"
        else:
            status = "Stable"

        return {
            "status": status,
            "operating_cash_flow": operating_cash_flow,
            "free_cash_flow": free_cash_flow,
            "trend": trend,
            "is_adequate": is_adequate,
            "periods_analyzed": periods_analyzed,
            "notes": notes,
        }

    # -----------------------------------------------------------------------
    # Week-3 Financial Indicator Extraction
    # -----------------------------------------------------------------------

    async def extract_financial_indicators(
        self,
        financial_text: str,
    ) -> dict:
        empty = {
            "interest_coverage": None,
            "operating_margin": None,
        }

        if not financial_text:
            return empty

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """
You are an expert financial analyst.

Extract ONLY:

1. Interest Coverage Ratio
2. Operating Margin

Rules:

- Recognize synonyms.
- If Interest Coverage is missing but EBIT and Interest Expense
  are present, calculate:

Interest Coverage = EBIT / Interest Expense

- If Operating Margin is missing but Operating Income and Revenue
  are available, calculate:

Operating Margin = (Operating Income / Revenue) * 100

Return ONLY valid JSON.

Example:

{
  "interest_coverage": 4.5,
  "operating_margin": 18.2
}

Return null when unavailable.
""",
                ),
                (
                    "user",
                    "{financial_text}",
                ),
            ]
        )

        params = {
            "financial_text": str(financial_text)[:10000],
        }

        if self.structured_llm is not None:
            try:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke(params)
                return result.model_dump()
            except Exception:
                pass

        # -------------------------------
        # Raw LLM fallback
        # -------------------------------
        if self.llm is not None:
            try:
                chain = prompt | self.llm
                response = await chain.ainvoke(params)

                raw_text = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )

                parsed = self._extract_json_from_text(raw_text)
                output = empty.copy()

                for field in (
                    "interest_coverage",
                    "operating_margin",
                ):
                    value = parsed.get(field)
                    try:
                        output[field] = (
                            float(value)
                            if value is not None
                            else None
                        )
                    except (TypeError, ValueError):
                        output[field] = None

                return output

            except Exception:
                pass

        return empty

    # -----------------------------------------------------------------------
    # Complete Financial Analysis
    # -----------------------------------------------------------------------

    async def analyze(
        self,
        financial_data: dict,
    ) -> dict:
        financial_data = financial_data or {}

        ratios = await self.compute_ratios(financial_data)
        cash_flow = await self.assess_cash_flow(financial_data)

        score = 50.0

        dscr = ratios["dscr"]
        current_ratio = ratios["current_ratio"]
        debt_to_equity = ratios["debt_to_equity"]

        # -------------------------------
        # DSCR Score
        # -------------------------------
        if dscr is not None:
            if dscr >= DSCR_SAFE_THRESHOLD:
                score += 25
            elif dscr >= DSCR_MIN_THRESHOLD:
                score += 10
            else:
                score -= 25

        # -------------------------------
        # Current Ratio Score
        # -------------------------------
        if current_ratio is not None:
            if current_ratio >= CURRENT_RATIO_STRONG:
                score += 15
            elif current_ratio >= CURRENT_RATIO_SAFE:
                score += 5
            else:
                score -= 15

        # -------------------------------
        # Debt-to-Equity Score
        # -------------------------------
        if debt_to_equity is not None:
            if 0 <= debt_to_equity <= 1:
                score += 10
            elif debt_to_equity > DE_RATIO_HIGH_RISK:
                score -= 15

        # -------------------------------
        # Cash Flow Score
        # -------------------------------
        if cash_flow["status"] == "Strong":
            score += 10
        elif cash_flow["status"] == "Weak":
            score -= 10

        score = max(0.0, min(score, 100.0))

        # ---------------------------------------------------------
        # Risk Level
        # ---------------------------------------------------------
        risk_level = self._classify_dscr(dscr)

        # ---------------------------------------------------------
        # Recommendation
        # ---------------------------------------------------------
        if risk_level == "Low":
            recommendation = (
                "Approval recommended. "
                "Financial health is strong with good liquidity, "
                "debt servicing capability, and positive cash flow."
            )
        elif risk_level == "Medium":
            recommendation = (
                "Conditional approval recommended. "
                "Review supporting financial documents before making "
                "the final lending decision."
            )
        else:
            recommendation = (
                "Not recommended due to weak financial health, "
                "poor debt servicing ability, or inadequate cash flow."
            )

        # ---------------------------------------------------------
        # Analysis Notes
        # ---------------------------------------------------------
        analysis_notes = []
        analysis_notes.extend(
            ratios.get("notes", [])
        )
        analysis_notes.extend(
            cash_flow.get("notes", [])
        )

        # ---------------------------------------------------------
        # Final Response
        # ---------------------------------------------------------
        return {
            "status": "success",
            "company_name": financial_data.get(
                "company_name",
                "Unknown",
            ),
            "financial_health_score": round(score, 2),
            "risk_level": risk_level,
            "ratios": ratios,
            "cash_flow_assessment": cash_flow,
            "recommendation": recommendation,
            "analysis_notes": analysis_notes,
        }