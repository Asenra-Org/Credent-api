# =============================================================================
# CREDENT — Unit Tests: GST/Bank Integrity Verification & Database Persistence
# Linear: ASE-31 [QA-W3]
# =============================================================================
"""
Unit tests covering:
    1. IntegrityVerificationAgent.cross_validate — GST vs Bank cross-validation logic
    2. Database writes — confirming GST/Bank integrity results (integrity_flags)
       are correctly persisted via save_appraisal() and retrievable via
       get_recent_appraisals()

All tests are MOCKED — Supabase is patched to return None so tests never make
a real network call (this is what gives "zero connection errors" even without
real Supabase credentials configured locally).

Run with:
    pytest tests/test_gst_bank_integration.py -v
"""

import pytest
from unittest.mock import patch
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent
from app.database.database import save_appraisal, get_recent_appraisals


@pytest.fixture
def integrity_agent() -> IntegrityVerificationAgent:
    return IntegrityVerificationAgent()


# ---------------------------------------------------------------------------
# 1. GST vs Bank Cross-Validation Logic
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
# 2. Database Writes — GST/Bank integrity_flags persistence (MOCKED, no network)
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
# 3. API Route — /api/v1/analysis/integrity-check (via FastAPI TestClient)
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
