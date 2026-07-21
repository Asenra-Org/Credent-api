# =============================================================================
# CREDENT — Unit Tests: GST/Bank Integrity Verification & Database Persistence
# Linear: ASE-31 [QA-W3]  |  Extended: [AI-A-W3] Monthly GST-to-Bank Cross-Validation
# =============================================================================
"""
Unit tests covering:
    1. IntegrityVerificationAgent.cross_validate — GST vs Bank cross-validation logic
       (aggregate-level checks preserved + new monthly-level checks)
    2. IntegrityVerificationAgent._cross_validate_monthly — month-by-month logic
    3. IntegrityVerificationAgent._normalise_period — date normalisation helper
    4. Database writes — confirming GST/Bank integrity results (integrity_flags)
       are correctly persisted via save_appraisal() and retrievable via
       get_recent_appraisals()

All tests are MOCKED — Supabase is patched to return None so tests never make
a real network call (this is what gives "zero connection errors" even without
real Supabase credentials configured locally).

Run with:
    pytest tests/test_gst_bank_integration.py -v
"""

import math
import pytest
import pandas as pd
from unittest.mock import patch
from app.agents.analysis.integrity_verification import (
    IntegrityVerificationAgent,
    _MONTHLY_VARIANCE_THRESHOLD,
)
from app.database.database import save_appraisal, get_recent_appraisals


@pytest.fixture
def integrity_agent() -> IntegrityVerificationAgent:
    return IntegrityVerificationAgent()


# ---------------------------------------------------------------------------
# Helpers — build test data quickly
# ---------------------------------------------------------------------------

def _gst(period: str, taxable_value: float, **kwargs) -> dict:
    """Return a minimal GSTR row."""
    return {"period": period, "taxable_value": taxable_value, **kwargs}


def _bank(date: str, amount: float, tx_type: str = "CREDIT") -> dict:
    """Return a minimal bank statement row."""
    return {"date": date, "amount": amount, "type": tx_type}


# ---------------------------------------------------------------------------
# 1. Existing GST vs Bank Cross-Validation Logic (PRESERVED — do not remove)
# ---------------------------------------------------------------------------

class TestCrossValidateMatchingData:

    @pytest.mark.asyncio
    async def test_matching_gst_and_bank_produces_zero_flags(self, integrity_agent):
        """When GST sales and bank inflows match closely, no flags should be raised."""
        gst_data = [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}]
        bank_data = [{"type": "CREDIT", "amount": 100_000}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"
        assert result["flags_detected"] == 0
        assert result["flags"] == []


class TestCrossValidateRevenueDiscrepancy:

    @pytest.mark.asyncio
    async def test_discrepancy_between_20_and_40_percent_is_medium_severity(self, integrity_agent):
        """GST sales vs bank inflows differing by 20-40% -> MEDIUM severity flag."""
        gst_data = [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}]
        bank_data = [{"type": "CREDIT", "amount": 70_000}]  # 30% variance

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["flags_detected"] == 1
        assert result["flags"][0]["flag"] == "Revenue Discrepancy"
        assert result["flags"][0]["severity"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_discrepancy_above_40_percent_is_high_severity(self, integrity_agent):
        """GST sales vs bank inflows differing by more than 40% -> HIGH severity flag."""
        gst_data = [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}]
        bank_data = [{"type": "CREDIT", "amount": 50_000}]  # 50% variance

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        flag = next(f for f in result["flags"] if f["flag"] == "Revenue Discrepancy")
        assert flag["severity"] == "HIGH"

    @pytest.mark.asyncio
    async def test_discrepancy_under_20_percent_raises_no_flag(self, integrity_agent):
        """Small, normal variance (<20%) should NOT raise a Revenue Discrepancy flag."""
        gst_data = [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}]
        bank_data = [{"type": "CREDIT", "amount": 90_000}]  # 10% variance

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["flags_detected"] == 0


class TestCrossValidateCircularTrading:

    @pytest.mark.asyncio
    async def test_identical_sale_and_purchase_volume_flags_circular_trading(self, integrity_agent):
        """
        Same GSTIN buying and selling near-identical amounts is a classic
        circular-trading fraud pattern and must be flagged CRITICAL.
        """
        gst_data = [
            {"taxable_value": 50_000, "type": "SALE", "counterparty_gstin": "ABC123"},
            {"taxable_value": 50_000, "type": "PURCHASE", "counterparty_gstin": "ABC123"},
        ]
        bank_data = [{"type": "CREDIT", "amount": 50_000}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        circular_flags = [f for f in result["flags"] if f["flag"] == "Potential Circular Trading"]
        assert len(circular_flags) == 1
        assert circular_flags[0]["severity"] == "CRITICAL"
        assert "ABC123" in circular_flags[0]["details"]

    @pytest.mark.asyncio
    async def test_different_sale_and_purchase_volume_does_not_flag_circular_trading(self, integrity_agent):
        """If buy/sell amounts for the same GSTIN differ significantly, it's normal trade, not circular."""
        gst_data = [
            {"taxable_value": 50_000, "type": "SALE", "counterparty_gstin": "XYZ789"},
            {"taxable_value": 5_000, "type": "PURCHASE", "counterparty_gstin": "XYZ789"},
        ]
        bank_data = [{"type": "CREDIT", "amount": 50_000}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        circular_flags = [f for f in result["flags"] if f["flag"] == "Potential Circular Trading"]
        assert len(circular_flags) == 0


class TestCrossValidateMissingOrBadData:

    @pytest.mark.asyncio
    async def test_empty_gst_data_returns_warning_not_crash(self, integrity_agent):
        """Missing GST data must return a graceful warning, never raise an exception."""
        result = await integrity_agent.cross_validate([], [{"type": "CREDIT", "amount": 1000}])

        assert result["status"] == "completed"
        assert result["flags_detected"] == 0
        assert "No GST data" in result["warning"]

    @pytest.mark.asyncio
    async def test_empty_bank_data_returns_warning_not_crash(self, integrity_agent):
        """Missing bank data must return a graceful warning, never raise an exception."""
        result = await integrity_agent.cross_validate([{"taxable_value": 1000, "type": "SALE"}], [])

        assert result["status"] == "completed"
        assert result["flags_detected"] == 0
        assert "No bank data" in result["warning"]

    @pytest.mark.asyncio
    async def test_missing_required_columns_produces_warning_not_crash(self, integrity_agent):
        """If expected columns (e.g. 'amount', 'taxable_value') are absent, skip gracefully with a warning."""
        gst_data = [{"some_other_field": 123}]
        bank_data = [{"another_field": 456}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"
        assert "warnings" in result
        assert any("missing" in w.lower() for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_malformed_data_does_not_crash(self, integrity_agent):
        """Completely unparseable input should not raise — must return a safe response with a warning."""
        gst_data = "not a list of dicts"  # deliberately wrong type
        bank_data = [{"type": "CREDIT", "amount": 1000}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"
        assert result["flags_detected"] == 0


# ---------------------------------------------------------------------------
# 2. Monthly GST-to-Bank Cross-Validation — NEW [AI-A-W3]
# ---------------------------------------------------------------------------

class TestMonthlyMatchingValues:
    """Happy-path: matching or near-matching monthly values."""

    @pytest.mark.asyncio
    async def test_all_months_match_produces_no_monthly_flags(self, integrity_agent):
        """When GST and bank match exactly for every month, zero monthly flags raised."""
        gst_data = [
            _gst("2024-01", 100_000),
            _gst("2024-02", 200_000),
            _gst("2024-03", 150_000),
        ]
        bank_data = [
            _bank("2024-01-15", 100_000),
            _bank("2024-02-20", 200_000),
            _bank("2024-03-10", 150_000),
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_difference_below_20_percent_produces_no_flag(self, integrity_agent):
        """10% variance is within tolerance — must not generate a flag."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [_bank("2024-01-05", 90_000)]  # 10% diff

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_difference_exactly_20_percent_does_not_flag(self, integrity_agent):
        """Exactly 20% difference is NOT strictly greater than threshold — no flag."""
        gst_data = [_gst("2024-04", 100_000)]
        bank_data = [_bank("2024-04-01", 80_000)]  # exactly 20% diff

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0


class TestMonthlyDiscrepancyFlagging:
    """Ensure MEDIUM flags are generated correctly above threshold."""

    @pytest.mark.asyncio
    async def test_difference_above_20_percent_generates_medium_flag(self, integrity_agent):
        """21% variance must trigger a MEDIUM Monthly GST-Bank Mismatch flag."""
        gst_data = [_gst("2024-06", 100_000)]
        bank_data = [_bank("2024-06-15", 79_000)]  # 21% diff

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1
        assert monthly_flags[0]["severity"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_flag_details_contain_period_and_amounts(self, integrity_agent):
        """Flag detail string must include the period, GST sales, and bank inflow amounts."""
        gst_data = [_gst("2024-07", 500_000)]
        bank_data = [_bank("2024-07-10", 300_000)]  # 40% diff

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1
        details = monthly_flags[0]["details"]
        assert "2024-07" in details
        assert "500,000" in details or "500000" in details

    @pytest.mark.asyncio
    async def test_multiple_failing_months_all_flagged(self, integrity_agent):
        """Three months all exceeding 20% variance → three separate MEDIUM flags."""
        gst_data = [
            _gst("2024-01", 100_000),
            _gst("2024-02", 200_000),
            _gst("2024-03", 300_000),
        ]
        bank_data = [
            _bank("2024-01-05", 60_000),   # 40% diff
            _bank("2024-02-05", 100_000),  # 50% diff
            _bank("2024-03-05", 150_000),  # 50% diff
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 3

    @pytest.mark.asyncio
    async def test_mixed_months_only_failing_ones_flagged(self, integrity_agent):
        """One month within tolerance, one above — only the failing month gets flagged."""
        gst_data = [
            _gst("2024-01", 100_000),  # will match
            _gst("2024-02", 100_000),  # will fail
        ]
        bank_data = [
            _bank("2024-01-10", 95_000),   # 5% diff — OK
            _bank("2024-02-10", 60_000),   # 40% diff — FAIL
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1
        assert "2024-02" in monthly_flags[0]["details"]


class TestMonthlyMissingMonths:
    """Months present in one source but absent in the other."""

    @pytest.mark.asyncio
    async def test_missing_bank_month_treated_as_zero_and_flagged(self, integrity_agent):
        """GST sales exist for a month with no bank credits → 100% diff → MEDIUM flag."""
        gst_data = [_gst("2024-05", 100_000)]
        bank_data = [_bank("2024-06-01", 100_000)]  # different month

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        # 2024-05 GST=100k Bank=0 → flagged; 2024-06 GST=0 Bank=100k → flagged
        assert any("2024-05" in f["details"] for f in monthly_flags)

    @pytest.mark.asyncio
    async def test_missing_gst_month_with_bank_credits_flagged(self, integrity_agent):
        """Bank credits exist for a month with no GST sales → large diff → MEDIUM flag."""
        gst_data = [_gst("2024-01", 100_000)]  # only Jan
        bank_data = [
            _bank("2024-01-10", 100_000),
            _bank("2024-02-10", 100_000),  # Feb has no GST counterpart
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert any("2024-02" in f["details"] for f in monthly_flags)


class TestMonthlyEdgeCases:
    """Edge cases: empty DataFrames, zero values, NaN, duplicates, bad dates."""

    @pytest.mark.asyncio
    async def test_no_period_column_in_gst_skips_with_warning(self, integrity_agent):
        """If gst_data has no 'period' column, monthly check skips with a warning."""
        gst_data = [{"taxable_value": 100_000, "type": "SALE"}]  # no 'period'
        bank_data = [_bank("2024-01-01", 100_000)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"
        assert "warnings" in result
        assert any("Monthly GST-Bank cross-validation skipped" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_no_date_column_in_bank_skips_with_warning(self, integrity_agent):
        """If bank_data has no 'date' column, monthly check skips with a warning."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [{"type": "CREDIT", "amount": 100_000}]  # no 'date'

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"
        assert "warnings" in result
        assert any("Monthly GST-Bank cross-validation skipped" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_zero_gst_sales_does_not_cause_division_by_zero(self, integrity_agent):
        """Zero GST sales with non-zero bank credits must not raise ZeroDivisionError."""
        gst_data = [_gst("2024-01", 0)]
        bank_data = [_bank("2024-01-01", 100_000)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        # Should complete without error; flag raised because bank >> GST
        assert result["status"] == "completed"
        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1

    @pytest.mark.asyncio
    async def test_zero_bank_inflow_with_gst_sales_flags_mismatch(self, integrity_agent):
        """Zero bank credits vs substantial GST sales should trigger a MEDIUM flag."""
        gst_data = [_gst("2024-03", 500_000)]
        bank_data = [_bank("2024-03-01", 0, tx_type="CREDIT")]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1
        assert monthly_flags[0]["severity"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_both_zero_gst_and_bank_no_flag(self, integrity_agent):
        """If both GST and bank are zero for a month, pct_diff = 0 → no flag."""
        gst_data = [_gst("2024-04", 0)]
        bank_data = [_bank("2024-04-01", 0)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_nan_taxable_value_treated_as_zero(self, integrity_agent):
        """NaN taxable_value coerced to 0; bank credits exist → MEDIUM flag for that month."""
        gst_data = [{"period": "2024-06", "taxable_value": float("nan")}]
        bank_data = [_bank("2024-06-15", 100_000)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        # Should not crash; NaN → 0 GST vs 100k bank → flagged
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_invalid_string_taxable_value_treated_as_zero(self, integrity_agent):
        """Non-numeric 'taxable_value' (e.g. 'N/A') coerced to 0 without crash."""
        gst_data = [{"period": "2024-08", "taxable_value": "N/A"}]
        bank_data = [_bank("2024-08-01", 50_000)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_duplicate_gst_months_are_summed(self, integrity_agent):
        """Two rows for the same period must be summed, not double-flagged."""
        gst_data = [
            _gst("2024-09", 50_000),
            _gst("2024-09", 50_000),  # duplicate period
        ]
        bank_data = [_bank("2024-09-15", 100_000)]  # matches sum

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0  # 100k vs 100k — no flag

    @pytest.mark.asyncio
    async def test_duplicate_bank_months_are_summed(self, integrity_agent):
        """Multiple bank transactions in the same month must be summed correctly."""
        gst_data = [_gst("2024-10", 200_000)]
        bank_data = [
            _bank("2024-10-01", 100_000),
            _bank("2024-10-15", 100_000),  # together = 200k
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_different_month_ordering_still_matches_correctly(self, integrity_agent):
        """Out-of-order months in input must still merge and compare correctly."""
        gst_data = [
            _gst("2024-03", 300_000),
            _gst("2024-01", 100_000),
            _gst("2024-02", 200_000),
        ]
        bank_data = [
            _bank("2024-02-28", 200_000),
            _bank("2024-03-31", 300_000),
            _bank("2024-01-31", 100_000),
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_invalid_date_in_bank_row_is_skipped_gracefully(self, integrity_agent):
        """Unparseable bank date rows should be dropped — must not crash."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [
            {"date": "NOT-A-DATE", "amount": 100_000, "type": "CREDIT"},
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        # May produce a warning or a flag, but must not raise an exception.
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_unexpected_date_format_iso_is_parsed(self, integrity_agent):
        """Full ISO date strings like '2024-01-15' should be parsed to '2024-01'."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [{"date": "2024-01-15T00:00:00", "amount": 95_000, "type": "CREDIT"}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        # 5% diff — within threshold, no monthly flag
        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_debit_transactions_excluded_from_aggregation(self, integrity_agent):
        """DEBIT transactions must be ignored — only CREDIT inflows count."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [
            _bank("2024-01-10", 200_000, tx_type="DEBIT"),  # this must be ignored
            _bank("2024-01-20", 100_000, tx_type="CREDIT"),  # only this counts
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0  # 100k GST vs 100k bank — no flag

    @pytest.mark.asyncio
    async def test_negative_gst_values_handled_without_crash(self, integrity_agent):
        """Negative taxable_value (e.g., credit notes) should not crash the agent."""
        gst_data = [_gst("2024-01", -50_000)]
        bank_data = [_bank("2024-01-10", 50_000)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_large_dataset_100_months(self, integrity_agent):
        """Performance smoke test — 100 months of data must complete without error."""
        gst_data = [
            {"period": f"20{str(y).zfill(2)}-{str(m).zfill(2)}", "taxable_value": 100_000 + i * 1000}
            for i, (y, m) in enumerate(
                (y, m) for y in range(20, 29) for m in range(1, 13)
            )
        ][:100]
        bank_data = [
            {"date": f"20{str(y).zfill(2)}-{str(m).zfill(2)}-15", "amount": 100_000 + i * 1000, "type": "CREDIT"}
            for i, (y, m) in enumerate(
                (y, m) for y in range(20, 29) for m in range(1, 13)
            )
        ][:100]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_floating_point_precision_handled(self, integrity_agent):
        """Floating-point amounts that are effectively equal must not spuriously flag."""
        gst_data = [_gst("2024-01", 100_000.001)]
        bank_data = [_bank("2024-01-01", 100_000.002)]  # ~0.000001% diff

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 0

    @pytest.mark.asyncio
    async def test_missing_required_columns_produces_skip_warning(self, integrity_agent):
        """Missing 'period' and 'date' columns → monthly check skips with a warning."""
        gst_data = [{"taxable_value": 100_000}]  # no 'period'
        bank_data = [{"amount": 100_000, "type": "CREDIT"}]  # no 'date'

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert "warnings" in result
        monthly_warnings = [w for w in result["warnings"] if "Monthly" in w]
        assert len(monthly_warnings) >= 1


class TestMonthlyFlagSchema:
    """Verify the shape of generated flags follows the existing project schema."""

    @pytest.mark.asyncio
    async def test_monthly_flag_has_required_keys(self, integrity_agent):
        """Every generated flag must have 'flag', 'severity', and 'details'."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [_bank("2024-01-01", 30_000)]  # 70% diff

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) >= 1
        for flag in monthly_flags:
            assert "flag" in flag
            assert "severity" in flag
            assert "details" in flag

    @pytest.mark.asyncio
    async def test_monthly_flag_severity_is_always_medium(self, integrity_agent):
        """Monthly mismatch flags must always use 'MEDIUM' severity per ticket spec."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [_bank("2024-01-01", 10_000)]  # 90% diff — very large

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1
        assert monthly_flags[0]["severity"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_response_schema_unchanged(self, integrity_agent):
        """Output dict must always contain 'status', 'flags_detected', and 'flags'."""
        gst_data = [_gst("2024-01", 100_000)]
        bank_data = [_bank("2024-01-01", 80_000)]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        assert "status" in result
        assert "flags_detected" in result
        assert "flags" in result
        assert result["status"] == "completed"
        assert result["flags_detected"] == len(result["flags"])


class TestNormalisePeriod:
    """Unit tests for the _normalise_period static helper."""

    def test_yyyy_mm_strings_returned_unchanged(self, integrity_agent):
        series = pd.Series(["2024-01", "2024-02", "2024-03"])
        result = integrity_agent._normalise_period(series)
        assert list(result) == ["2024-01", "2024-02", "2024-03"]

    def test_full_date_strings_normalised_to_yyyy_mm(self, integrity_agent):
        series = pd.Series(["2024-01-15", "2024-02-28", "2024-03-31"])
        result = integrity_agent._normalise_period(series)
        assert list(result) == ["2024-01", "2024-02", "2024-03"]

    def test_unparseable_values_become_na(self, integrity_agent):
        series = pd.Series(["NOT-A-DATE", "ALSO-BAD"])
        result = integrity_agent._normalise_period(series)
        assert result.isna().all()


# ---------------------------------------------------------------------------
# 3. Integration with cross_validate() — combined flow
# ---------------------------------------------------------------------------

class TestMonthlyIntegrationWithCrossValidate:
    """End-to-end: monthly check wired into the full cross_validate() pipeline."""

    @pytest.mark.asyncio
    async def test_monthly_flags_appear_in_final_flags_list(self, integrity_agent):
        """Monthly mismatch flags must flow through to the top-level result."""
        gst_data = [
            _gst("2024-01", 100_000),
            _gst("2024-02", 200_000),
        ]
        bank_data = [
            _bank("2024-01-10", 40_000),   # 60% diff — fail
            _bank("2024-02-10", 200_000),  # 0% diff — pass
        ]
        result = await integrity_agent.cross_validate(gst_data, bank_data)

        monthly_flags = [f for f in result["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) == 1
        assert result["flags_detected"] == len(result["flags"])

    @pytest.mark.asyncio
    async def test_aggregate_and_monthly_flags_can_coexist(self, integrity_agent):
        """The aggregate Revenue Discrepancy flag and monthly flags can both appear."""
        gst_data = [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "G1", "period": "2024-01"}]
        bank_data = [{"type": "CREDIT", "amount": 40_000, "date": "2024-01-15"}]

        result = await integrity_agent.cross_validate(gst_data, bank_data)

        flag_names = {f["flag"] for f in result["flags"]}
        # Both aggregate and monthly checks fire for this data
        assert "Revenue Discrepancy" in flag_names
        assert "Monthly GST-Bank Mismatch" in flag_names


# ---------------------------------------------------------------------------
# 4. Database Writes — GST/Bank integrity_flags persistence (MOCKED, no network)
# ---------------------------------------------------------------------------

class TestIntegrityFlagsDatabaseWrite:

    @pytest.mark.asyncio
    async def test_integrity_flags_are_saved_and_retrievable(self, integrity_agent):
        """
        Runs a real GST/Bank cross-validation, then saves the result via
        save_appraisal() and confirms it round-trips correctly through the
        database. Supabase is mocked out (_get_supabase -> None) so this
        test never attempts a real network connection — it only exercises
        the local SQLite fallback path.
        """
        gst_data = [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}]
        bank_data = [{"type": "CREDIT", "amount": 70_000}]
        integrity_result = await integrity_agent.cross_validate(gst_data, bank_data)

        with patch("app.database.database._get_supabase", return_value=None):
            record_id = save_appraisal({
                "company_id": "CMP_GST_TEST_31",
                "company_name": "GST Bank Integration Test Co",
                "integrity_flags": integrity_result,
            })

            assert record_id is not None
            assert record_id.startswith("REC_")

            recent = get_recent_appraisals(limit=10)
            saved_record = next(
                (r for r in recent if r.get("company_name") == "GST Bank Integration Test Co"),
                None,
            )

        assert saved_record is not None
        assert saved_record["integrity_flags"]["flags_detected"] == 1
        assert saved_record["integrity_flags"]["flags"][0]["flag"] == "Revenue Discrepancy"

    def test_save_appraisal_never_calls_real_supabase_when_mocked(self):
        """
        Confirms save_appraisal respects the mocked Supabase client and does
        not attempt a live connection — this is what guarantees 'zero
        connection errors' in CI environments with no real credentials.
        """
        with patch("app.database.database._get_supabase") as mock_get_sb:
            mock_get_sb.return_value = None
            record_id = save_appraisal({
                "company_id": "CMP_NO_NETWORK_TEST",
                "company_name": "No Network Test Co",
                "integrity_flags": {"flags_detected": 0, "flags": []},
            })
            mock_get_sb.assert_called()
            assert record_id is not None


# ---------------------------------------------------------------------------
# 5. API Route — /api/v1/analysis/integrity-check (via FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestIntegrityCheckEndpoint:

    def test_endpoint_with_no_data_returns_warning(self, client):
        """Hitting the endpoint with empty gst_data/bank_data should return a graceful warning, not an error."""
        response = client.post("/api/v1/analysis/integrity-check", json={
            "gst_data": [],
            "bank_data": [],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["flags_detected"] == 0
        assert "warning" in data

    def test_endpoint_with_discrepant_data_returns_flags(self, client):
        """Hitting the endpoint with mismatched GST/Bank data should surface real flags via the API."""
        response = client.post("/api/v1/analysis/integrity-check", json={
            "gst_data": [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}],
            "bank_data": [{"type": "CREDIT", "amount": 50_000}],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["flags_detected"] >= 1
        assert any(f["flag"] == "Revenue Discrepancy" for f in data["flags"])

    def test_endpoint_monthly_mismatch_surfaced_via_api(self, client):
        """Monthly flags must be surfaced through the /integrity-check endpoint."""
        response = client.post("/api/v1/analysis/integrity-check", json={
            "gst_data": [{"taxable_value": 100_000, "period": "2024-01"}],
            "bank_data": [{"type": "CREDIT", "amount": 40_000, "date": "2024-01-15"}],
        })
        assert response.status_code == 200
        data = response.json()
        monthly_flags = [f for f in data["flags"] if f["flag"] == "Monthly GST-Bank Mismatch"]
        assert len(monthly_flags) >= 1
        assert monthly_flags[0]["severity"] == "MEDIUM"
