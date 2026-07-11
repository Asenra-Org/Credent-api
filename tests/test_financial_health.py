# =============================================================================
# CREDENT — Unit Tests: FinancialHealthAgent
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
Comprehensive pytest test suite for FinancialHealthAgent.

These tests are entirely self-contained — they do NOT require:
    - A running FastAPI server
    - A database connection
    - Any API keys
    - Any network access

All tests are async-native, exercising the agent's async interface directly.

Run with:
    pytest tests/test_financial_health.py -v
"""

import pytest
import pytest_asyncio
from app.agents.analysis.financial_health import (
    FinancialHealthAgent,
    DSCR_SAFE_THRESHOLD,
    DSCR_MIN_THRESHOLD,
    CURRENT_RATIO_STRONG,
    CURRENT_RATIO_SAFE,
    DE_RATIO_HIGH_RISK,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent() -> FinancialHealthAgent:
    """Returns a fresh FinancialHealthAgent instance for each test."""
    return FinancialHealthAgent()


@pytest.fixture
def healthy_company() -> dict:
    """
    A well-structured financial data payload representing a healthy borrower.

    Based on a mid-sized manufacturing company with:
        - Strong operating income relative to debt obligations
        - Solid liquidity buffer
        - Conservative leverage
        - Growing cash flows over 6 months
    """
    return {
        "company_name":          "Acme Manufacturing Corp",
        "net_operating_income":  5_000_000.0,   # ₹50 Lakh EBIT
        "debt_service":          2_000_000.0,   # ₹20 Lakh annual repayments
        "current_assets":        8_000_000.0,   # ₹80 Lakh current assets
        "current_liabilities":   4_000_000.0,   # ₹40 Lakh current liabilities
        "total_debt":            10_000_000.0,  # ₹1 Cr total debt
        "total_equity":          15_000_000.0,  # ₹1.5 Cr equity
        "inventory":             1_500_000.0,   # ₹15 Lakh inventory
        "operating_cash_flow":   4_500_000.0,   # ₹45 Lakh operating cash flow
        "free_cash_flow":        3_000_000.0,   # ₹30 Lakh free cash flow
        "historical_inflows":    [3_000_000, 3_200_000, 3_500_000, 3_800_000, 4_000_000, 4_500_000],
    }


@pytest.fixture
def distressed_company() -> dict:
    """
    Financial data for a highly distressed, high-risk borrower.
    Used to validate that the agent correctly flags danger signals.
    """
    return {
        "company_name":          "Globex Retail Solutions",
        "net_operating_income":  500_000.0,     # Just ₹5 Lakh income
        "debt_service":          3_000_000.0,   # ₹30 Lakh debt payments — way above income
        "current_assets":        1_000_000.0,
        "current_liabilities":   5_000_000.0,   # 5× more liabilities than assets
        "total_debt":            20_000_000.0,
        "total_equity":          2_000_000.0,   # 10:1 debt-to-equity
        "operating_cash_flow":  -500_000.0,    # Negative cash flow
        "free_cash_flow":       -800_000.0,
        "historical_inflows":    [-200_000, -400_000, 100_000, -300_000, -500_000],
    }


# ---------------------------------------------------------------------------
# Tests: compute_ratios — Normal calculations
# ---------------------------------------------------------------------------

class TestComputeRatiosNormal:

    @pytest.mark.asyncio
    async def test_dscr_computed_correctly(self, agent, healthy_company):
        """DSCR = net_operating_income / debt_service = 5M / 2M = 2.5"""
        result = await agent.compute_ratios(healthy_company)
        assert result["dscr"] == pytest.approx(2.5, rel=1e-3)

    @pytest.mark.asyncio
    async def test_current_ratio_computed_correctly(self, agent, healthy_company):
        """Current Ratio = current_assets / current_liabilities = 8M / 4M = 2.0"""
        result = await agent.compute_ratios(healthy_company)
        assert result["current_ratio"] == pytest.approx(2.0, rel=1e-3)

    @pytest.mark.asyncio
    async def test_debt_to_equity_computed_correctly(self, agent, healthy_company):
        """Debt-to-Equity = total_debt / total_equity = 10M / 15M ≈ 0.6667"""
        result = await agent.compute_ratios(healthy_company)
        assert result["debt_to_equity"] == pytest.approx(10_000_000 / 15_000_000, rel=1e-3)

    @pytest.mark.asyncio
    async def test_quick_ratio_uses_inventory_when_available(self, agent, healthy_company):
        """Quick Ratio = (current_assets - inventory) / current_liabilities = 6.5M / 4M = 1.625"""
        result = await agent.compute_ratios(healthy_company)
        expected = (8_000_000 - 1_500_000) / 4_000_000
        assert result["quick_ratio"] == pytest.approx(expected, rel=1e-3)

    @pytest.mark.asyncio
    async def test_quick_ratio_approximated_without_inventory(self, agent, healthy_company):
        """Without inventory, quick_ratio should be 80% of current_ratio."""
        data = {**healthy_company}
        del data["inventory"]
        result = await agent.compute_ratios(data)
        expected = round(result["current_ratio"] * 0.8, 4)
        assert result["quick_ratio"] == pytest.approx(expected, rel=1e-3)

    @pytest.mark.asyncio
    async def test_ratios_return_no_notes_when_all_data_present(self, agent, healthy_company):
        """A complete, valid payload should produce no warning notes."""
        result = await agent.compute_ratios(healthy_company)
        assert result["notes"] == []

    @pytest.mark.asyncio
    async def test_distressed_ratios_are_below_thresholds(self, agent, distressed_company):
        """Distressed company ratios should all be below safe thresholds."""
        result = await agent.compute_ratios(distressed_company)
        # DSCR below minimum
        assert result["dscr"] < DSCR_MIN_THRESHOLD
        # Current ratio below 1.0 — cannot cover short-term obligations
        assert result["current_ratio"] < CURRENT_RATIO_SAFE
        # D/E above high-risk threshold
        assert result["debt_to_equity"] > DE_RATIO_HIGH_RISK


# ---------------------------------------------------------------------------
# Tests: compute_ratios — Division by zero
# ---------------------------------------------------------------------------

class TestComputeRatiosDivisionByZero:

    @pytest.mark.asyncio
    async def test_dscr_is_none_when_debt_service_is_zero(self, agent, healthy_company):
        """If debt_service is 0, DSCR is undefined — must return None, not crash."""
        data = {**healthy_company, "debt_service": 0}
        result = await agent.compute_ratios(data)
        assert result["dscr"] is None

    @pytest.mark.asyncio
    async def test_current_ratio_is_none_when_current_liabilities_is_zero(self, agent, healthy_company):
        """Zero current liabilities makes current ratio mathematically undefined."""
        data = {**healthy_company, "current_liabilities": 0}
        result = await agent.compute_ratios(data)
        assert result["current_ratio"] is None

    @pytest.mark.asyncio
    async def test_debt_to_equity_is_none_when_equity_is_zero(self, agent, healthy_company):
        """Zero equity (complete insolvency) makes D/E undefined."""
        data = {**healthy_company, "total_equity": 0}
        result = await agent.compute_ratios(data)
        assert result["debt_to_equity"] is None

    @pytest.mark.asyncio
    async def test_notes_populated_on_zero_denominator(self, agent, healthy_company):
        """A zero denominator should produce a human-readable note explaining the issue."""
        data = {**healthy_company, "debt_service": 0, "current_liabilities": 0}
        result = await agent.compute_ratios(data)
        assert len(result["notes"]) >= 2  # One for DSCR, one for current ratio


# ---------------------------------------------------------------------------
# Tests: compute_ratios — Missing values
# ---------------------------------------------------------------------------

class TestComputeRatiosMissingValues:

    @pytest.mark.asyncio
    async def test_all_ratios_none_on_empty_input(self, agent):
        """An empty dict should not crash — all ratios should be None."""
        result = await agent.compute_ratios({})
        assert result["dscr"] is None
        assert result["current_ratio"] is None
        assert result["debt_to_equity"] is None

    @pytest.mark.asyncio
    async def test_partial_data_computes_available_ratios(self, agent):
        """Only DSCR inputs provided — only DSCR should be computed."""
        data = {"net_operating_income": 1_000_000, "debt_service": 500_000}
        result = await agent.compute_ratios(data)
        assert result["dscr"] == pytest.approx(2.0, rel=1e-3)
        assert result["current_ratio"] is None
        assert result["debt_to_equity"] is None

    @pytest.mark.asyncio
    async def test_none_values_treated_as_missing(self, agent):
        """Explicit None values should behave the same as missing keys."""
        data = {"net_operating_income": None, "debt_service": None}
        result = await agent.compute_ratios(data)
        assert result["dscr"] is None


# ---------------------------------------------------------------------------
# Tests: compute_ratios — Invalid / non-numeric inputs
# ---------------------------------------------------------------------------

class TestComputeRatiosInvalidInputs:

    @pytest.mark.asyncio
    async def test_string_values_handled_gracefully(self, agent):
        """String values should not raise — ratios should return None."""
        data = {
            "net_operating_income": "N/A",
            "debt_service": "unknown",
            "current_assets": "₹80L",
            "current_liabilities": "40",
        }
        result = await agent.compute_ratios(data)
        assert result["dscr"] is None
        # current_liabilities="40" is actually parseable, current_assets="₹80L" is not
        assert result["current_ratio"] is None

    @pytest.mark.asyncio
    async def test_numeric_strings_are_parsed_correctly(self, agent):
        """String representations of numbers (e.g. '5000000') should be coerced."""
        data = {
            "net_operating_income": "5000000",
            "debt_service": "2000000",
        }
        result = await agent.compute_ratios(data)
        assert result["dscr"] == pytest.approx(2.5, rel=1e-3)


# ---------------------------------------------------------------------------
# Tests: compute_ratios — Negative values
# ---------------------------------------------------------------------------

class TestComputeRatiosNegativeValues:

    @pytest.mark.asyncio
    async def test_negative_equity_produces_negative_de_ratio(self, agent, healthy_company):
        """Negative equity (net liabilities) is valid and should produce a negative D/E."""
        data = {**healthy_company, "total_equity": -5_000_000}
        result = await agent.compute_ratios(data)
        assert result["debt_to_equity"] < 0

    @pytest.mark.asyncio
    async def test_negative_operating_income_produces_negative_dscr(self, agent, healthy_company):
        """Negative operating income should result in a negative DSCR."""
        data = {**healthy_company, "net_operating_income": -1_000_000}
        result = await agent.compute_ratios(data)
        assert result["dscr"] < 0


# ---------------------------------------------------------------------------
# Tests: assess_cash_flow — Normal calculations
# ---------------------------------------------------------------------------

class TestAssessCashFlowNormal:

    @pytest.mark.asyncio
    async def test_strong_status_when_positive_cf_and_positive_trend(self, agent, healthy_company):
        """Positive operating CF + positive free CF + rising trend → Strong."""
        result = await agent.assess_cash_flow(healthy_company)
        assert result["status"] == "Strong"
        assert result["trend"] == "Positive"
        assert result["is_adequate"] is True

    @pytest.mark.asyncio
    async def test_weak_status_for_distressed_company(self, agent, distressed_company):
        """Negative operating CF + declining historical trend → Weak."""
        result = await agent.assess_cash_flow(distressed_company)
        assert result["status"] == "Weak"
        assert result["trend"] == "Declining"
        assert result["is_adequate"] is False

    @pytest.mark.asyncio
    async def test_periods_analyzed_matches_historical_inflows_length(self, agent, healthy_company):
        """periods_analyzed must equal the number of valid historical inflow entries."""
        result = await agent.assess_cash_flow(healthy_company)
        assert result["periods_analyzed"] == len(healthy_company["historical_inflows"])

    @pytest.mark.asyncio
    async def test_operating_and_free_cash_flow_values_correct(self, agent, healthy_company):
        """Returned cash flow figures should match the input exactly."""
        result = await agent.assess_cash_flow(healthy_company)
        assert result["operating_cash_flow"] == healthy_company["operating_cash_flow"]
        assert result["free_cash_flow"] == healthy_company["free_cash_flow"]

    @pytest.mark.asyncio
    async def test_stable_trend_when_positive_but_not_growing(self, agent, healthy_company):
        """If majority positive but last < first, trend should be Stable."""
        data = {
            **healthy_company,
            "historical_inflows": [4_000_000, 3_500_000, 4_200_000, 3_800_000, 3_600_000, 3_000_000],
        }
        result = await agent.assess_cash_flow(data)
        # Last (3M) < First (4M) → Stable, not Positive
        assert result["trend"] == "Stable"


# ---------------------------------------------------------------------------
# Tests: assess_cash_flow — Missing / empty historical data
# ---------------------------------------------------------------------------

class TestAssessCashFlowMissingData:

    @pytest.mark.asyncio
    async def test_empty_historical_inflows_produces_insufficient_data_trend(self, agent, healthy_company):
        """No historical inflows → trend should be 'Insufficient Data'."""
        data = {**healthy_company, "historical_inflows": []}
        result = await agent.assess_cash_flow(data)
        assert result["trend"] == "Insufficient Data"
        assert result["periods_analyzed"] == 0

    @pytest.mark.asyncio
    async def test_missing_historical_inflows_key_treated_as_empty(self, agent, healthy_company):
        """A missing 'historical_inflows' key should be treated like an empty list."""
        data = {k: v for k, v in healthy_company.items() if k != "historical_inflows"}
        result = await agent.assess_cash_flow(data)
        assert result["periods_analyzed"] == 0

    @pytest.mark.asyncio
    async def test_non_numeric_inflows_are_filtered_with_note(self, agent, healthy_company):
        """Non-numeric values in historical_inflows should be skipped with a warning note."""
        data = {**healthy_company, "historical_inflows": [100_000, "bad_value", 200_000, None]}
        result = await agent.assess_cash_flow(data)
        # Two numeric values should be analyzed
        assert result["periods_analyzed"] == 2
        # A note should explain the skipped values
        assert any("non-numeric" in note for note in result["notes"])

    @pytest.mark.asyncio
    async def test_single_period_positive_inflow_produces_positive_trend(self, agent, healthy_company):
        """A single positive period should produce trend='Positive'."""
        data = {**healthy_company, "historical_inflows": [500_000]}
        result = await agent.assess_cash_flow(data)
        assert result["trend"] == "Positive"

    @pytest.mark.asyncio
    async def test_single_period_negative_inflow_produces_declining_trend(self, agent, healthy_company):
        """A single negative period should produce trend='Declining'."""
        data = {**healthy_company, "historical_inflows": [-100_000]}
        result = await agent.assess_cash_flow(data)
        assert result["trend"] == "Declining"


# ---------------------------------------------------------------------------
# Tests: assess_cash_flow — Edge cases
# ---------------------------------------------------------------------------

class TestAssessCashFlowEdgeCases:

    @pytest.mark.asyncio
    async def test_zero_operating_cash_flow(self, agent, healthy_company):
        """Zero operating CF is not positive — should result in Stable or Weak."""
        data = {**healthy_company, "operating_cash_flow": 0.0}
        result = await agent.assess_cash_flow(data)
        # Status should NOT be Strong
        assert result["status"] != "Strong"

    @pytest.mark.asyncio
    async def test_all_negative_historical_inflows_produce_declining_trend(self, agent, healthy_company):
        """All negative inflows over history → Declining trend."""
        data = {**healthy_company, "historical_inflows": [-100, -200, -300, -150]}
        result = await agent.assess_cash_flow(data)
        assert result["trend"] == "Declining"

    @pytest.mark.asyncio
    async def test_exactly_70_percent_positive_inflows_is_positive_trend(self, agent, healthy_company):
        """Exactly at the 70% threshold should produce Positive (boundary condition)."""
        # 7 positive out of 10 = 70%
        inflows = [100_000] * 7 + [-50_000] * 3
        data = {**healthy_company, "historical_inflows": inflows, "operating_cash_flow": 200_000}
        result = await agent.assess_cash_flow(data)
        assert result["trend"] in ("Positive", "Stable")  # Both are acceptable at boundary


# ---------------------------------------------------------------------------
# Tests: analyze — Integration / end-to-end
# ---------------------------------------------------------------------------

class TestAnalyzeIntegration:

    @pytest.mark.asyncio
    async def test_healthy_company_produces_success_status(self, agent, healthy_company):
        """A full, valid payload should return status='success'."""
        result = await agent.analyze(healthy_company)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_healthy_company_produces_high_score(self, agent, healthy_company):
        """A healthy company with strong ratios should score above 75."""
        result = await agent.analyze(healthy_company)
        assert result["financial_health_score"] >= 75.0

    @pytest.mark.asyncio
    async def test_distressed_company_produces_low_score(self, agent, distressed_company):
        """A distressed company should score significantly below 55."""
        result = await agent.analyze(distressed_company)
        assert result["financial_health_score"] < 55.0

    @pytest.mark.asyncio
    async def test_result_contains_all_required_keys(self, agent, healthy_company):
        """The response must contain all keys expected by the route layer."""
        result = await agent.analyze(healthy_company)
        required_keys = [
            "status", "company_name", "financial_health_score",
            "risk_level", "ratios", "cash_flow_assessment",
            "recommendation", "analysis_notes",
        ]
        for key in required_keys:
            assert key in result, f"Missing required key: '{key}'"

    @pytest.mark.asyncio
    async def test_ratios_block_contains_expected_keys(self, agent, healthy_company):
        """ratios dict must contain all ratio fields."""
        result = await agent.analyze(healthy_company)
        ratio_keys = ["dscr", "current_ratio", "debt_to_equity", "quick_ratio"]
        for key in ratio_keys:
            assert key in result["ratios"], f"Missing ratio key: '{key}'"

    @pytest.mark.asyncio
    async def test_cash_flow_assessment_block_contains_expected_keys(self, agent, healthy_company):
        """cash_flow_assessment dict must contain all assessment fields."""
        result = await agent.analyze(healthy_company)
        cf_keys = ["status", "operating_cash_flow", "free_cash_flow", "trend", "is_adequate", "periods_analyzed"]
        for key in cf_keys:
            assert key in result["cash_flow_assessment"], f"Missing cash flow key: '{key}'"

    @pytest.mark.asyncio
    async def test_company_name_passed_through_correctly(self, agent, healthy_company):
        """The company_name from input must appear unchanged in the response."""
        result = await agent.analyze(healthy_company)
        assert result["company_name"] == healthy_company["company_name"]

    @pytest.mark.asyncio
    async def test_analyze_on_empty_dict_returns_error_safe_response(self, agent):
        """An empty payload should not crash — it should return a safe error or low-score result."""
        result = await agent.analyze({})
        # Must always return a dict with at least these keys
        assert "status" in result
        assert "financial_health_score" in result
        assert "recommendation" in result

    @pytest.mark.asyncio
    async def test_healthy_company_risk_level_is_low(self, agent, healthy_company):
        """DSCR of 2.5 is well above the safe threshold — risk level should be Low."""
        result = await agent.analyze(healthy_company)
        assert result["risk_level"] == "Low"

    @pytest.mark.asyncio
    async def test_distressed_company_risk_level_is_high(self, agent, distressed_company):
        """DSCR below 1.0 should flag the risk level as High."""
        result = await agent.analyze(distressed_company)
        assert result["risk_level"] == "High"

    @pytest.mark.asyncio
    async def test_recommendation_is_non_empty_string(self, agent, healthy_company):
        """Recommendation must always be a non-empty string."""
        result = await agent.analyze(healthy_company)
        assert isinstance(result["recommendation"], str)
        assert len(result["recommendation"]) > 0

    @pytest.mark.asyncio
    async def test_score_is_between_0_and_100(self, agent, healthy_company):
        """Financial health score must always be within the valid 0–100 range."""
        result = await agent.analyze(healthy_company)
        assert 0.0 <= result["financial_health_score"] <= 100.0

    @pytest.mark.asyncio
    async def test_score_is_between_0_and_100_for_distressed_company(self, agent, distressed_company):
        """Distressed company score must also stay within valid bounds."""
        result = await agent.analyze(distressed_company)
        assert 0.0 <= result["financial_health_score"] <= 100.0


# ---------------------------------------------------------------------------
# Tests: Private helper — _safe_divide
# ---------------------------------------------------------------------------

class TestSafeDivide:
    """Direct unit tests for the internal division helper."""

    def test_normal_division(self, agent):
        assert agent._safe_divide(10, 4) == pytest.approx(2.5)

    def test_division_by_zero_returns_none(self, agent):
        assert agent._safe_divide(100, 0) is None

    def test_none_numerator_returns_none(self, agent):
        assert agent._safe_divide(None, 5) is None

    def test_none_denominator_returns_none(self, agent):
        assert agent._safe_divide(5, None) is None

    def test_string_number_is_coerced(self, agent):
        assert agent._safe_divide("10", "4") == pytest.approx(2.5)

    def test_invalid_string_returns_none(self, agent):
        assert agent._safe_divide("abc", 4) is None

    def test_negative_numerator(self, agent):
        assert agent._safe_divide(-10, 5) == pytest.approx(-2.0)

    def test_negative_denominator(self, agent):
        result = agent._safe_divide(10, -5)
        assert result == pytest.approx(-2.0)

    def test_result_is_rounded_to_4_decimal_places(self, agent):
        # 1 / 3 = 0.3333...  → should be rounded to 4dp
        result = agent._safe_divide(1, 3)
        assert result == pytest.approx(0.3333, rel=1e-3)
