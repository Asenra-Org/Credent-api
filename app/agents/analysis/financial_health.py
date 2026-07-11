# =============================================================================
# CREDENT — Financial Health Agent (Ratio Analysis & Cash Flow Assessment)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
FinancialHealthAgent

This agent receives structured financial data about a borrower and performs
three core tasks:
    1. Computes key financial ratios (DSCR, Current Ratio, Debt-to-Equity).
    2. Evaluates whether the company's cash flows are healthy or concerning.
    3. Aggregates both results into a single, clean analysis response.

The agent is intentionally stateless — it holds no memory between calls.
Every method receives all the data it needs as a parameter, making the
agent safe to use concurrently across multiple requests.

Input shape expected in financial_data:
    {
        "current_assets":       float   — e.g. total cash + receivables + inventory
        "current_liabilities":  float   — e.g. short-term debts + payables due
        "total_debt":           float   — e.g. all outstanding loans and bonds
        "total_equity":         float   — e.g. shareholder funds / net worth
        "net_operating_income": float   — e.g. EBIT (Earnings Before Interest & Tax)
        "debt_service":         float   — e.g. annual principal + interest repayments
        "operating_cash_flow":  float   — e.g. net cash from operations this period
        "free_cash_flow":       float   — e.g. operating cash flow minus capex
        "historical_inflows":   list[float] — e.g. [monthly/annual cash inflows over N periods]
        "company_name":         str     — optional, for identification in the response
    }
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring thresholds — sourced from standard credit underwriting norms.
# All thresholds are declared here (not scattered in logic) so they are
# easy to audit, adjust, or override in future by the risk team.
# ---------------------------------------------------------------------------

# DSCR: A value above 1.25 is considered safe. Below 1.0 means the business
#        cannot service its debt from operating income alone — a red flag.
DSCR_SAFE_THRESHOLD: float = 1.25
DSCR_MIN_THRESHOLD: float = 1.0

# Current Ratio: Above 2.0 is excellent liquidity. Below 1.0 means the
#                company cannot cover short-term obligations.
CURRENT_RATIO_STRONG: float = 2.0
CURRENT_RATIO_SAFE: float = 1.0

# Debt-to-Equity: Industry varies, but above 2.0 is generally considered
#                 high leverage. Below 1.0 is conservative and healthy.
DE_RATIO_HIGH_RISK: float = 2.0
DE_RATIO_MODERATE: float = 1.0

# Cash flow trend: If fewer than this fraction of historical periods show
# positive inflows, the trend is flagged as declining.
CASH_FLOW_POSITIVE_PERIOD_RATIO: float = 0.7  # 70% of periods must be positive


class FinancialHealthAgent:
    """
    Analyzes structured financial data to assess the financial health of a borrower.

    Methods are all async to align with the project's FastAPI / async architecture,
    even though these computations are purely CPU-bound. This keeps the interface
    consistent and allows future I/O (e.g. logging to a database) without refactoring.
    """

    def __init__(self) -> None:
        # No external dependencies at init time. Intentionally lightweight so
        # the agent can be safely instantiated at module load (see analysis.py).
        pass

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _safe_divide(self, numerator: Any, denominator: Any) -> Optional[float]:
        """
        Divide two numbers safely.

        Returns None if:
            - Either value is None, not a number, or cannot be converted to float.
            - The denominator is zero.

        Returning None (instead of 0 or infinity) is intentional — it signals
        to the caller that the ratio is *undeterminable*, not that it is zero.
        The calling code can then surface this as 'N/A' to the user.
        """
        try:
            n = float(numerator)
            d = float(denominator)
        except (TypeError, ValueError):
            # Non-numeric input — log once and return None gracefully.
            logger.warning(
                "FinancialHealthAgent._safe_divide received non-numeric input: "
                "numerator=%s, denominator=%s", numerator, denominator
            )
            return None

        if d == 0.0:
            # Division by zero — mathematically undefined, treat as unknown.
            logger.debug(
                "FinancialHealthAgent._safe_divide: denominator is zero. "
                "Returning None to avoid ZeroDivisionError."
            )
            return None

        return round(n / d, 4)

    def _to_float(self, value: Any) -> float:
        """
        Convert a value to float, returning 0.0 on failure.

        Used for values that feed into additive logic (e.g. operating cash flow)
        where a missing value should be treated as zero — not as unknown.
        Contrast with _safe_divide which uses None for missing ratio components.
        """
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _classify_dscr(self, dscr: Optional[float]) -> str:
        """
        Translate a numeric DSCR into a human-readable risk label.
        Used to populate the 'risk_level' field in the analysis response.
        """
        if dscr is None:
            return "Undetermined"
        if dscr >= DSCR_SAFE_THRESHOLD:
            return "Low"
        if dscr >= DSCR_MIN_THRESHOLD:
            return "Medium"
        return "High"

    def _compute_overall_score(
        self,
        dscr: Optional[float],
        current_ratio: Optional[float],
        de_ratio: Optional[float],
        cash_flow_status: str,
    ) -> float:
        """
        Produce a single 0–100 financial health score by weighting the four
        key indicators.

        Weights (must sum to 100):
            - DSCR:            40 pts  (primary measure of debt repayment capacity)
            - Current Ratio:   25 pts  (short-term liquidity)
            - D/E Ratio:       20 pts  (leverage / balance sheet risk)
            - Cash Flow:       15 pts  (operational quality)

        Scoring bands are intentionally lenient at the boundaries to avoid
        cliff-edge results from small rounding differences in inputs.
        """
        score: float = 0.0

        # --- DSCR (40 points) ---
        if dscr is not None:
            if dscr >= 2.0:
                score += 40.0
            elif dscr >= DSCR_SAFE_THRESHOLD:
                score += 30.0
            elif dscr >= DSCR_MIN_THRESHOLD:
                score += 15.0
            else:
                score += 5.0

        # --- Current Ratio (25 points) ---
        if current_ratio is not None:
            if current_ratio >= CURRENT_RATIO_STRONG:
                score += 25.0
            elif current_ratio >= CURRENT_RATIO_SAFE:
                score += 15.0
            else:
                score += 5.0

        # --- Debt-to-Equity (20 points) — lower D/E is better ---
        if de_ratio is not None:
            if de_ratio < DE_RATIO_MODERATE:
                score += 20.0
            elif de_ratio < DE_RATIO_HIGH_RISK:
                score += 12.0
            else:
                score += 4.0

        # --- Cash Flow (15 points) ---
        if cash_flow_status == "Strong":
            score += 15.0
        elif cash_flow_status == "Stable":
            score += 10.0
        elif cash_flow_status == "Weak":
            score += 3.0
        # 'Insufficient Data' contributes 0 points

        return round(score, 2)

    def _build_recommendation(self, score: float, risk_level: str) -> str:
        """
        Convert the overall score and risk label into a plain-English
        lending recommendation that a credit officer can act on immediately.
        """
        if score >= 75.0:
            return (
                "Financial profile is strong. Recommended for credit approval "
                "with standard interest terms."
            )
        if score >= 55.0:
            return (
                f"Borderline financial health (score: {score}). "
                "Manual review recommended before credit approval. "
                "Consider collateral or guarantor requirements."
            )
        return (
            f"High financial risk detected (score: {score}, risk level: {risk_level}). "
            "Credit approval is not recommended without significant risk mitigation."
        )

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    async def compute_ratios(self, financial_data: dict) -> dict:
        """
        Compute the three key financial ratios used in credit underwriting:
            - Debt Service Coverage Ratio (DSCR)
            - Current Ratio
            - Debt-to-Equity Ratio

        Args:
            financial_data (dict): Structured financial inputs from the borrower's
                                   documents. Missing keys default to None (not zero)
                                   so the absence of data is distinguishable from
                                   a genuine zero value.

        Returns:
            dict: {
                "dscr":             float | None,
                "current_ratio":    float | None,
                "debt_to_equity":   float | None,
                "quick_ratio":      float | None,  # bonus ratio, computed if data available
                "notes":            list[str]       # human-readable warnings for None ratios
            }
        """
        notes: list[str] = []

        # Extract all required fields. We use .get() with None default so we
        # can distinguish "field missing" from "field present but zero."
        net_operating_income = financial_data.get("net_operating_income")
        debt_service         = financial_data.get("debt_service")
        current_assets       = financial_data.get("current_assets")
        current_liabilities  = financial_data.get("current_liabilities")
        total_debt           = financial_data.get("total_debt")
        total_equity         = financial_data.get("total_equity")

        # ---- 1. Debt Service Coverage Ratio (DSCR) -------------------------
        # Formula: Net Operating Income / Total Debt Service
        # A DSCR of 1.0 means the company earns exactly enough to cover debt.
        # Below 1.0 means it cannot — a lending red flag.
        dscr = self._safe_divide(net_operating_income, debt_service)
        if dscr is None:
            notes.append(
                "DSCR could not be computed: 'net_operating_income' or "
                "'debt_service' is missing, zero, or non-numeric."
            )

        # ---- 2. Current Ratio -----------------------------------------------
        # Formula: Current Assets / Current Liabilities
        # Measures ability to meet short-term obligations. A value < 1 means
        # the company would struggle to pay bills due within 12 months.
        current_ratio = self._safe_divide(current_assets, current_liabilities)
        if current_ratio is None:
            notes.append(
                "Current Ratio could not be computed: 'current_assets' or "
                "'current_liabilities' is missing, zero, or non-numeric."
            )

        # ---- 3. Debt-to-Equity Ratio ----------------------------------------
        # Formula: Total Debt / Total Equity
        # Shows how much debt the company uses relative to shareholder funds.
        # High D/E = high leverage = more risk for the lender.
        debt_to_equity = self._safe_divide(total_debt, total_equity)
        if debt_to_equity is None:
            notes.append(
                "Debt-to-Equity Ratio could not be computed: 'total_debt' or "
                "'total_equity' is missing, zero, or non-numeric."
            )

        # ---- 4. Quick Ratio (bonus, best-effort) ----------------------------
        # Formula: (Current Assets - Inventory) / Current Liabilities
        # More conservative than current ratio — excludes illiquid inventory.
        # We compute this only if inventory data is available; otherwise skip.
        inventory = financial_data.get("inventory")
        if inventory is not None and current_assets is not None:
            liquid_assets = self._to_float(current_assets) - self._to_float(inventory)
            quick_ratio = self._safe_divide(liquid_assets, current_liabilities)
        else:
            # Approximate quick ratio from current ratio if inventory is unknown.
            # This is a reasonable fallback used by many credit scoring systems.
            quick_ratio = round(self._to_float(current_ratio) * 0.8, 4) if current_ratio is not None else None

        logger.info(
            "compute_ratios completed — DSCR: %s, Current: %s, D/E: %s, Quick: %s",
            dscr, current_ratio, debt_to_equity, quick_ratio
        )

        return {
            "dscr":           dscr,
            "current_ratio":  current_ratio,
            "debt_to_equity": debt_to_equity,
            "quick_ratio":    quick_ratio,
            "notes":          notes,
        }

    async def assess_cash_flow(self, financial_data: dict) -> dict:
        """
        Evaluate the adequacy and trend of the borrower's cash flows.

        Two levels of assessment are performed:
            1. Point-in-time: Is the most recent operating cash flow positive?
            2. Historical trend: Are the majority of historical inflow periods positive?

        Args:
            financial_data (dict): Structured financial inputs.
                Expected keys:
                    - operating_cash_flow (float): Current period net cash from operations.
                    - free_cash_flow (float): Operating cash flow minus capital expenditures.
                    - historical_inflows (list[float]): Cash inflows for each historical
                      period (e.g., monthly or annual). Used to detect trends.

        Returns:
            dict: {
                "status":               str    — "Strong" | "Stable" | "Weak" | "Insufficient Data"
                "operating_cash_flow":  float,
                "free_cash_flow":       float,
                "trend":                str    — "Positive" | "Stable" | "Declining" | "Insufficient Data"
                "is_adequate":          bool   — True if cash flows meet minimum adequacy threshold
                "periods_analyzed":     int    — number of historical periods assessed
                "notes":                list[str]
            }
        """
        notes: list[str] = []

        operating_cash_flow = self._to_float(financial_data.get("operating_cash_flow"))
        free_cash_flow      = self._to_float(financial_data.get("free_cash_flow"))
        historical_inflows: list = financial_data.get("historical_inflows") or []

        # ---- 1. Analyze historical trend ------------------------------------
        # Filter out any non-numeric entries defensively. In practice these come
        # from PDF parsing which can occasionally produce garbage values.
        valid_inflows = []
        for item in historical_inflows:
            try:
                valid_inflows.append(float(item))
            except (TypeError, ValueError):
                notes.append(f"Ignored non-numeric historical inflow value: {item!r}")

        periods_analyzed = len(valid_inflows)

        if periods_analyzed == 0:
            # No historical data — we can only comment on current period.
            trend = "Insufficient Data"
            notes.append(
                "No valid historical inflow data provided. "
                "Trend analysis requires at least one historical period."
            )
        elif periods_analyzed == 1:
            # Single data point — can't determine direction, only polarity.
            trend = "Positive" if valid_inflows[0] > 0 else "Declining"
            notes.append("Only one historical period available; trend direction is approximate.")
        else:
            # Count how many periods had positive inflows.
            positive_periods = sum(1 for v in valid_inflows if v > 0)
            positive_ratio = positive_periods / periods_analyzed

            if positive_ratio >= CASH_FLOW_POSITIVE_PERIOD_RATIO:
                # Also check if the last period is better than the first
                # (rising trend within the already-positive majority).
                trend = "Positive" if valid_inflows[-1] >= valid_inflows[0] else "Stable"
            else:
                trend = "Declining"

        # ---- 2. Determine overall cash flow status --------------------------
        # We cross-reference current operating cash flow with historical trend
        # to arrive at a holistic status label.
        if operating_cash_flow > 0 and free_cash_flow > 0 and trend == "Positive":
            status = "Strong"
            is_adequate = True
        elif operating_cash_flow > 0 and trend in ("Positive", "Stable", "Insufficient Data"):
            status = "Stable"
            is_adequate = True
        elif operating_cash_flow <= 0 and trend == "Declining":
            status = "Weak"
            is_adequate = False
            notes.append(
                "Operating cash flow is non-positive AND historical trend is declining. "
                "This is a significant repayment risk."
            )
        else:
            # Mixed signals — current is negative but trend isn't consistently bad,
            # or other ambiguous combinations.
            status = "Weak"
            is_adequate = operating_cash_flow > 0

        logger.info(
            "assess_cash_flow completed — status: %s, trend: %s, "
            "operating_cf: %s, free_cf: %s, periods: %d",
            status, trend, operating_cash_flow, free_cash_flow, periods_analyzed,
        )

        return {
            "status":              status,
            "operating_cash_flow": operating_cash_flow,
            "free_cash_flow":      free_cash_flow,
            "trend":               trend,
            "is_adequate":         is_adequate,
            "periods_analyzed":    periods_analyzed,
            "notes":               notes,
        }

    async def analyze(self, financial_data: dict) -> dict:
        """
        Run a full financial health analysis for a borrower.

        This is the top-level method called by the route layer. It orchestrates:
            Step 1 — Compute financial ratios
            Step 2 — Assess cash flow adequacy and trend
            Step 3 — Derive an overall health score (0–100)
            Step 4 — Determine risk level and lending recommendation

        The method never raises. On unexpected errors it returns a safe error
        response that the route layer can return directly to the client.

        Args:
            financial_data (dict): See module docstring for full key reference.

        Returns:
            dict: Full analysis result matching FinancialHealthResponse schema
                  defined in app/routes/analysis.py.
        """
        company_name = str(financial_data.get("company_name", "Unknown Company"))

        try:
            # --- Step 1: Compute ratios -------------------------------------
            ratios = await self.compute_ratios(financial_data)

            # --- Step 2: Assess cash flows ----------------------------------
            cash_flow = await self.assess_cash_flow(financial_data)

            # --- Step 3: Overall health score (0–100) -----------------------
            health_score = self._compute_overall_score(
                dscr=ratios["dscr"],
                current_ratio=ratios["current_ratio"],
                de_ratio=ratios["debt_to_equity"],
                cash_flow_status=cash_flow["status"],
            )

            # --- Step 4: Risk classification and recommendation -------------
            risk_level = self._classify_dscr(ratios["dscr"])
            recommendation = self._build_recommendation(health_score, risk_level)

            # Collect all analysis notes into one consolidated list so the
            # consumer gets a single place to look for warnings.
            all_notes = ratios.get("notes", []) + cash_flow.get("notes", [])

            logger.info(
                "analyze completed for '%s' — score: %.2f, risk: %s",
                company_name, health_score, risk_level,
            )

            return {
                "status":                "success",
                "company_name":          company_name,
                "financial_health_score": health_score,
                "risk_level":            risk_level,
                "ratios": {
                    "dscr":                    ratios["dscr"],
                    "current_ratio":           ratios["current_ratio"],
                    "debt_to_equity":          ratios["debt_to_equity"],
                    "quick_ratio":             ratios["quick_ratio"],
                },
                "cash_flow_assessment": {
                    "status":              cash_flow["status"],
                    "operating_cash_flow": cash_flow["operating_cash_flow"],
                    "free_cash_flow":      cash_flow["free_cash_flow"],
                    "trend":               cash_flow["trend"],
                    "is_adequate":         cash_flow["is_adequate"],
                    "periods_analyzed":    cash_flow["periods_analyzed"],
                },
                "recommendation": recommendation,
                "analysis_notes": all_notes,
            }

        except Exception as exc:
            # We never let an exception escape to the route layer.
            # Log it for the engineering team and return a safe fallback.
            logger.exception(
                "FinancialHealthAgent.analyze raised an unexpected error for '%s': %s",
                company_name, exc,
            )
            return {
                "status":                "error",
                "company_name":          company_name,
                "financial_health_score": 0.0,
                "risk_level":            "Undetermined",
                "ratios":                {},
                "cash_flow_assessment":  {},
                "recommendation":        "Analysis could not be completed due to an internal error.",
                "analysis_notes":        [f"Internal error: {str(exc)}"],
            }
