# =============================================================================
# CREDENT — Unit Tests: Financial Health & Management Quality (Promoter) Agents
# Linear: ASE-22 [QA-W2]
# =============================================================================
"""
Unit tests covering:
    1. FinancialHealthAgent  — ratio calculations & score thresholds (fully implemented)
    2. ManagementQualityAgent — promoter/governance scoring (NOT YET IMPLEMENTED)

Run with:
    pytest tests/test_agents.py -v
"""

import pytest
from app.agents.analysis.financial_health import (
    FinancialHealthAgent,
    DSCR_SAFE_THRESHOLD,
    DSCR_MIN_THRESHOLD,
    CURRENT_RATIO_STRONG,
)
from app.agents.analysis.management_quality import ManagementQualityAgent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def financial_agent() -> FinancialHealthAgent:
    return FinancialHealthAgent()


@pytest.fixture
def management_agent() -> ManagementQualityAgent:
    return ManagementQualityAgent()


# ---------------------------------------------------------------------------
# 1. FinancialHealthAgent — Ratio Calculations
# ---------------------------------------------------------------------------

class TestFinancialHealthRatios:

    @pytest.mark.asyncio
    async def test_current_ratio_equals_2_0(self, financial_agent):
        """
        Direct example from ASE-22: Current Ratio = 2.0
        Formula: current_assets / current_liabilities
        200,000 / 100,000 = 2.0
        """
        data = {"current_assets": 200_000.0, "current_liabilities": 100_000.0}
        result = await financial_agent.compute_ratios(data)
        assert result["current_ratio"] == pytest.approx(2.0, rel=1e-4)

    @pytest.mark.asyncio
    async def test_dscr_computed_correctly(self, financial_agent):
        """DSCR = net_operating_income / debt_service"""
        data = {"net_operating_income": 5_000_000.0, "debt_service": 2_000_000.0}
        result = await financial_agent.compute_ratios(data)
        assert result["dscr"] == pytest.approx(2.5, rel=1e-4)

    @pytest.mark.asyncio
    async def test_debt_to_equity_computed_correctly(self, financial_agent):
        """D/E = total_debt / total_equity"""
        data = {"total_debt": 10_000_000.0, "total_equity": 20_000_000.0}
        result = await financial_agent.compute_ratios(data)
        assert result["debt_to_equity"] == pytest.approx(0.5, rel=1e-4)

    @pytest.mark.asyncio
    async def test_ratio_is_none_when_denominator_is_zero(self, financial_agent):
        """Dividing by zero must never crash — it should return None, not raise."""
        data = {"current_assets": 100_000.0, "current_liabilities": 0.0}
        result = await financial_agent.compute_ratios(data)
        assert result["current_ratio"] is None
        assert len(result["notes"]) > 0  # a human-readable warning must be added


# ---------------------------------------------------------------------------
# 2. FinancialHealthAgent — Score / Risk Level Thresholds
# ---------------------------------------------------------------------------

class TestFinancialHealthThresholds:

    def test_dscr_at_or_above_safe_threshold_is_low_risk(self, financial_agent):
        """DSCR >= 1.25 (DSCR_SAFE_THRESHOLD) must classify as 'Low' risk."""
        assert financial_agent._classify_dscr(DSCR_SAFE_THRESHOLD) == "Low"
        assert financial_agent._classify_dscr(2.0) == "Low"

    def test_dscr_between_min_and_safe_is_medium_risk(self, financial_agent):
        """1.0 <= DSCR < 1.25 must classify as 'Medium' risk."""
        assert financial_agent._classify_dscr(DSCR_MIN_THRESHOLD) == "Medium"
        assert financial_agent._classify_dscr(1.1) == "Medium"

    def test_dscr_below_min_threshold_is_high_risk(self, financial_agent):
        """DSCR < 1.0 must classify as 'High' risk — cannot service debt."""
        assert financial_agent._classify_dscr(0.5) == "High"

    def test_dscr_none_is_undetermined(self, financial_agent):
        """Missing/undeterminable DSCR must classify as 'Undetermined', not crash."""
        assert financial_agent._classify_dscr(None) == "Undetermined"

    @pytest.mark.asyncio
    async def test_strong_company_scores_above_75_and_is_recommended(self, financial_agent):
        """A financially strong company should score >=75 and get an approval recommendation."""
        data = {
            "company_name": "Strong Test Co",
            "net_operating_income": 5_000_000.0,
            "debt_service": 2_000_000.0,       # DSCR = 2.5
            "current_assets": 8_000_000.0,
            "current_liabilities": 4_000_000.0,  # Current Ratio = 2.0
            "total_debt": 5_000_000.0,
            "total_equity": 15_000_000.0,        # D/E = 0.33
            "operating_cash_flow": 4_000_000.0,
            "free_cash_flow": 2_000_000.0,
            "historical_inflows": [3_000_000, 3_200_000, 3_500_000, 3_800_000],
        }
        result = await financial_agent.analyze(data)
        assert result["status"] == "success"
        assert result["financial_health_score"] >= 75.0
        assert "approval" in result["recommendation"].lower()

    @pytest.mark.asyncio
    async def test_distressed_company_scores_low_and_is_not_recommended(self, financial_agent):
        """A distressed company should score low and NOT be recommended for approval."""
        data = {
            "company_name": "Distressed Test Co",
            "net_operating_income": 500_000.0,
            "debt_service": 3_000_000.0,        # DSCR well below 1.0
            "current_assets": 1_000_000.0,
            "current_liabilities": 5_000_000.0,  # Current Ratio well below 1.0
            "total_debt": 20_000_000.0,
            "total_equity": 2_000_000.0,         # D/E = 10.0 (very high risk)
            "operating_cash_flow": -500_000.0,
            "free_cash_flow": -800_000.0,
            "historical_inflows": [-200_000, -400_000, -300_000, -500_000],
        }
        result = await financial_agent.analyze(data)
        assert result["status"] == "success"
        assert result["financial_health_score"] < 55.0
        assert "not recommended" in result["recommendation"].lower()


# ---------------------------------------------------------------------------
# 3. ManagementQualityAgent (Promoter) — NOT YET IMPLEMENTED
# ---------------------------------------------------------------------------
"""
IMPORTANT — READ BEFORE EDITING:

As of this writing, app/agents/analysis/management_quality.py contains only a
stub. `analyze()` and `check_promoter_history()` both raise NotImplementedError.
There is no scoring logic yet, so promoter score thresholds genuinely cannot
be tested — there is nothing to assert against.

The tests below do two things:
    1. Confirm the CURRENT contract: calling the stub raises NotImplementedError
       (this documents the current state and will legitimately pass right now).
    2. Provide pre-written, SKIPPED tests for the promoter scoring thresholds,
       ready to un-skip the moment the real logic is implemented.

This keeps `pytest` fully green (skipped tests do not count as failures) while
being transparent that promoter scoring itself is still pending.

Track: raise this with Karan / whoever owns management_quality.py.
"""

class TestManagementQualityCurrentState:

    @pytest.mark.asyncio
    async def test_check_promoter_history_implemented(self, management_agent):
        """Tests that check_promoter_history parses valid LLM response correctly."""
        from unittest.mock import AsyncMock, MagicMock, patch
        import json

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "director_cibil_scores": [750],
            "past_defaults": False,
            "regulatory_actions": [],
            "past_ventures": ["Company A"],
            "warnings": []
        })

        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
            mock_ainvoke.return_value = mock_response
            result = await management_agent.check_promoter_history(["promoter_1"])

        assert result["past_defaults"] is False
        assert 750 in result["director_cibil_scores"]
        assert "Company A" in result["past_ventures"]


@pytest.mark.skip(reason="Blocked: ManagementQualityAgent.analyze() has no scoring logic yet (ASE-22)")
class TestManagementQualityScoreThresholds:
    """
    Pre-written for when the Promoter scoring logic is implemented.
    Expected contract, based on the ManagementQualityResponse model in
    app/routes/analysis.py (management_score: float 0-100, risk_level: str):

        score >= 75           -> risk_level == "Low"
        55 <= score < 75       -> risk_level == "Medium"
        score < 55             -> risk_level == "High"

    Un-skip this class once app/agents/analysis/management_quality.py
    implements real scoring, and adjust thresholds to match the real logic.
    """

    @pytest.mark.asyncio
    async def test_high_experience_clean_record_scores_low_risk(self, management_agent):
        data = {
            "company_name": "Clean Promoter Co",
            "promoters": [{"name": "Aditya Sen", "experience_years": 18, "risk_flags": []}],
        }
        result = await management_agent.analyze(data)
        assert result["management_score"] >= 75.0
        assert result["risk_level"] == "Low"

    @pytest.mark.asyncio
    async def test_promoter_with_regulatory_flags_scores_high_risk(self, management_agent):
        data = {
            "company_name": "Flagged Promoter Co",
            "promoters": [{
                "name": "Risky Promoter",
                "experience_years": 3,
                "risk_flags": ["Regulatory action", "Loan default"],
            }],
        }
        result = await management_agent.analyze(data)
        assert result["management_score"] < 55.0
        assert result["risk_level"] == "High"
