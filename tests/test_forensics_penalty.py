# =============================================================================
# CREDENT — Unit & Integration Tests: Forensics Penalty Mapper (AI-A-W4)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
Tests for the pikepdf forensics → credit score penalty integration introduced
in ticket AI-A-W4.

Coverage matrix (all acceptance criteria):
    ✓ is_suspicious = True  → score reduced by exactly 15
    ✓ is_suspicious = False → no penalty
    ✓ Missing is_suspicious field
    ✓ None forensics input
    ✓ Empty dict forensics input
    ✓ Multiple suspicious flags (penalty applied exactly once)
    ✓ Invalid data types for is_suspicious
    ✓ Score exactly 15
    ✓ Score below 15 → floor at 0
    ✓ Score = 0 → stays 0
    ✓ Score never becomes negative
    ✓ Existing scoring logic (FinancialHealthAgent) unchanged
    ✓ Response schema unchanged (all existing keys present)
    ✓ Integration test: full scoring pipeline via FastAPI TestClient

Run with:
    pytest tests/test_forensics_penalty.py -v
"""

import os
import pytest

# Ensure no real API key is required for import-time ChatGroq construction.
os.environ.setdefault("GROQ_API_KEY", "test-dummy-key-does-not-make-network-calls")

from app.routes.documents import apply_forensics_penalty, FORENSICS_PENALTY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _suspicious_forensics(**overrides) -> dict:
    """Return a standard suspicious forensics dict with optional overrides."""
    base = {
        "is_suspicious": True,
        "flags": ["UNNATURAL_SOURCE: Created via photoshop"],
        "metadata": {"creator": "Adobe Photoshop", "producer": "iLovePDF"},
    }
    base.update(overrides)
    return base


def _clean_forensics(**overrides) -> dict:
    """Return a standard clean forensics dict with optional overrides."""
    base = {
        "is_suspicious": False,
        "flags": [],
        "metadata": {"creator": "Microsoft Word", "producer": "Microsoft Word"},
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. Core Penalty Behaviour
# ===========================================================================

class TestForensicsPenaltyCoreLogic:
    """Direct unit tests for apply_forensics_penalty()."""

    # -----------------------------------------------------------------------
    # Acceptance criterion: is_suspicious = True → deduct exactly 15 pts
    # -----------------------------------------------------------------------

    def test_suspicious_true_deducts_exactly_15_points(self):
        """Primary acceptance criterion: 15-point deduction when tampering detected."""
        result = apply_forensics_penalty(80, _suspicious_forensics())
        assert result["adjusted_score"] == 80 - FORENSICS_PENALTY  # 65
        assert result["penalty_applied"] is True
        assert result["penalty_points"] == FORENSICS_PENALTY  # 15

    def test_suspicious_true_original_score_preserved(self):
        """original_score must always reflect the pre-penalty value."""
        result = apply_forensics_penalty(75, _suspicious_forensics())
        assert result["original_score"] == 75
        assert result["adjusted_score"] == 60

    # -----------------------------------------------------------------------
    # Acceptance criterion: is_suspicious = False → no penalty
    # -----------------------------------------------------------------------

    def test_suspicious_false_no_penalty(self):
        """Clean document: score must be completely unchanged."""
        result = apply_forensics_penalty(70, _clean_forensics())
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False
        assert result["penalty_points"] == 0
        assert result["original_score"] == 70

    def test_suspicious_false_with_multiple_flags_still_no_penalty(self):
        """Even if flags list is non-empty, is_suspicious=False means no deduction."""
        forensics = _clean_forensics(
            is_suspicious=False,
            flags=["ERROR: Could not verify document integrity"],
        )
        result = apply_forensics_penalty(85, forensics)
        assert result["adjusted_score"] == 85
        assert result["penalty_applied"] is False

    # -----------------------------------------------------------------------
    # Acceptance criterion: score never becomes negative
    # -----------------------------------------------------------------------

    def test_score_exactly_15_becomes_zero(self):
        """base_score == FORENSICS_PENALTY: 15 - 15 = 0, not -anything."""
        result = apply_forensics_penalty(15, _suspicious_forensics())
        assert result["adjusted_score"] == 0
        assert result["penalty_applied"] is True

    def test_score_below_15_floors_at_zero(self):
        """base_score < penalty (e.g. 10): result must be 0, never negative."""
        result = apply_forensics_penalty(10, _suspicious_forensics())
        assert result["adjusted_score"] == 0

    def test_score_zero_stays_zero(self):
        """base_score = 0: already at floor, must remain 0."""
        result = apply_forensics_penalty(0, _suspicious_forensics())
        assert result["adjusted_score"] == 0
        assert result["adjusted_score"] >= 0

    def test_score_1_floors_at_zero_not_negative(self):
        """base_score = 1 with penalty=15: floor at 0."""
        result = apply_forensics_penalty(1, _suspicious_forensics())
        assert result["adjusted_score"] == 0
        assert result["adjusted_score"] >= 0

    def test_high_score_penalty_is_correct(self):
        """Typical good borrower score (90): 90 - 15 = 75."""
        result = apply_forensics_penalty(90, _suspicious_forensics())
        assert result["adjusted_score"] == 75
        assert result["penalty_applied"] is True

    # -----------------------------------------------------------------------
    # Acceptance criterion: penalty applied exactly once
    # (even when forensics has many flags)
    # -----------------------------------------------------------------------

    def test_multiple_suspicious_flags_deduct_15_exactly_once(self):
        """Multiple flags in forensics.flags must not compound the penalty."""
        forensics = _suspicious_forensics(flags=[
            "UNNATURAL_SOURCE: Created via photoshop",
            "TAMPER_WARNING: Document was modified after creation",
        ])
        result = apply_forensics_penalty(60, forensics)
        # Still only 15 deducted — NOT 30
        assert result["adjusted_score"] == 45
        assert result["penalty_points"] == FORENSICS_PENALTY


# ===========================================================================
# 2. Edge Cases — Missing / Malformed Forensics Input
# ===========================================================================

class TestForensicsPenaltyEdgeCases:
    """Defensive behaviour: system must never crash; default to no penalty."""

    def test_none_forensics_returns_score_unchanged(self):
        """None input: cannot determine suspicion, score unchanged."""
        result = apply_forensics_penalty(70, None)
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_empty_dict_returns_score_unchanged(self):
        """Empty dict: is_suspicious key absent, treated as not suspicious."""
        result = apply_forensics_penalty(70, {})
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_missing_is_suspicious_key_returns_unchanged(self):
        """Dict without is_suspicious: default to no penalty."""
        forensics = {"flags": [], "metadata": {}}
        result = apply_forensics_penalty(70, forensics)
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_is_suspicious_none_value_returns_unchanged(self):
        """Explicit None value for is_suspicious: default to no penalty."""
        forensics = {"is_suspicious": None, "flags": [], "metadata": {}}
        result = apply_forensics_penalty(70, forensics)
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_forensics_is_string_returns_score_unchanged(self):
        """Completely wrong type (str): must not crash."""
        result = apply_forensics_penalty(70, "not_a_dict")  # type: ignore[arg-type]
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_forensics_is_list_returns_score_unchanged(self):
        """Wrong type (list): must not crash."""
        result = apply_forensics_penalty(70, [True])  # type: ignore[arg-type]
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_forensics_is_integer_returns_score_unchanged(self):
        """Wrong type (int): must not crash."""
        result = apply_forensics_penalty(70, 1)  # type: ignore[arg-type]
        assert result["adjusted_score"] == 70
        assert result["penalty_applied"] is False

    def test_is_suspicious_truthy_integer_applies_penalty(self):
        """is_suspicious=1 (truthy): bool(1) == True → penalty should apply."""
        result = apply_forensics_penalty(80, {"is_suspicious": 1})
        assert result["adjusted_score"] == 65
        assert result["penalty_applied"] is True

    def test_is_suspicious_falsy_integer_no_penalty(self):
        """is_suspicious=0 (falsy): bool(0) == False → no penalty."""
        result = apply_forensics_penalty(80, {"is_suspicious": 0})
        assert result["adjusted_score"] == 80
        assert result["penalty_applied"] is False

    def test_is_suspicious_truthy_string_applies_penalty(self):
        """is_suspicious='true' (truthy non-empty str): penalty applies."""
        result = apply_forensics_penalty(80, {"is_suspicious": "true"})
        assert result["adjusted_score"] == 65
        assert result["penalty_applied"] is True

    def test_is_suspicious_empty_string_no_penalty(self):
        """is_suspicious='' (falsy str): no penalty."""
        result = apply_forensics_penalty(80, {"is_suspicious": ""})
        assert result["adjusted_score"] == 80
        assert result["penalty_applied"] is False


# ===========================================================================
# 3. Edge Cases — Malformed base_score
# ===========================================================================

class TestForensicsPenaltyMalformedBaseScore:
    """base_score edge cases: non-numeric, None, negative."""

    def test_base_score_none_treated_as_zero(self):
        """None base_score: coerce to 0, penalty brings 0 → 0 (floor)."""
        result = apply_forensics_penalty(None, _suspicious_forensics())  # type: ignore[arg-type]
        assert result["adjusted_score"] == 0
        assert result["adjusted_score"] >= 0

    def test_base_score_string_treated_as_zero(self):
        """String base_score: coerce to 0 gracefully."""
        result = apply_forensics_penalty("not_a_number", _suspicious_forensics())  # type: ignore[arg-type]
        assert result["adjusted_score"] == 0
        assert result["penalty_applied"] is True  # tampering detected, penalty still applies

    def test_base_score_negative_clamped_before_penalty(self):
        """Negative base_score: clamp to 0 before arithmetic; result stays 0."""
        result = apply_forensics_penalty(-10, _suspicious_forensics())
        assert result["adjusted_score"] == 0
        assert result["adjusted_score"] >= 0

    def test_base_score_float_converted_to_int(self):
        """Float base_score: int() truncation, then normal penalty."""
        result = apply_forensics_penalty(80.9, _suspicious_forensics())  # type: ignore[arg-type]
        assert result["adjusted_score"] == int(80.9) - FORENSICS_PENALTY  # 65


# ===========================================================================
# 4. Return Schema Validation
# ===========================================================================

class TestForensicsPenaltyReturnSchema:
    """apply_forensics_penalty() must always return a dict with exactly 4 keys."""

    EXPECTED_KEYS = {"original_score", "adjusted_score", "penalty_applied", "penalty_points"}

    def test_schema_present_when_penalty_applied(self):
        result = apply_forensics_penalty(80, _suspicious_forensics())
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_schema_present_when_no_penalty(self):
        result = apply_forensics_penalty(80, _clean_forensics())
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_schema_present_on_none_input(self):
        result = apply_forensics_penalty(80, None)
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_schema_present_on_empty_dict(self):
        result = apply_forensics_penalty(80, {})
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_adjusted_score_is_always_int(self):
        """adjusted_score must be an int, not float."""
        result = apply_forensics_penalty(80, _suspicious_forensics())
        assert isinstance(result["adjusted_score"], int)

    def test_penalty_applied_is_always_bool(self):
        result = apply_forensics_penalty(80, _suspicious_forensics())
        assert isinstance(result["penalty_applied"], bool)

    def test_penalty_points_is_always_int(self):
        result = apply_forensics_penalty(80, _suspicious_forensics())
        assert isinstance(result["penalty_points"], int)


# ===========================================================================
# 5. FORENSICS_PENALTY Constant Validation
# ===========================================================================

class TestForensicsPenaltyConstant:
    """Verify the module constant is exactly what the ticket specifies."""

    def test_forensics_penalty_constant_is_15(self):
        """Acceptance criterion: deduct EXACTLY 15 points. Must never be changed
        without a ticket-level decision."""
        assert FORENSICS_PENALTY == 15

    def test_forensics_penalty_constant_is_int(self):
        """Penalty must be an integer (no float arithmetic surprises)."""
        assert isinstance(FORENSICS_PENALTY, int)


# ===========================================================================
# 6. Existing FinancialHealthAgent Scoring Is Unchanged
# ===========================================================================

class TestExistingFinancialHealthScoringUnchanged:
    """
    Regression guard: the forensics penalty must NOT alter FinancialHealthAgent
    behaviour at all. These tests confirm the existing scoring contract is intact.
    """

    @pytest.mark.asyncio
    async def test_financial_health_agent_score_not_affected(self):
        """FinancialHealthAgent.analyze() must still produce the same score
        it did before AI-A-W4. The forensics penalty only touches base_score
        inside the documents route — never the financial_health_score."""
        from app.agents.analysis.financial_health import FinancialHealthAgent
        agent = FinancialHealthAgent()
        data = {
            "company_name": "Regression Test Co",
            "net_operating_income": 5_000_000.0,
            "debt_service": 2_000_000.0,
            "current_assets": 8_000_000.0,
            "current_liabilities": 4_000_000.0,
            "total_debt": 5_000_000.0,
            "total_equity": 15_000_000.0,
            "operating_cash_flow": 4_000_000.0,
            "free_cash_flow": 2_000_000.0,
            "historical_inflows": [3_000_000, 3_200_000, 3_500_000],
        }
        result = await agent.analyze(data)
        assert result["status"] == "success"
        # Score must be >= 75 for a strong company — unchanged
        assert result["financial_health_score"] >= 75.0
        # The penalty key must NOT appear in FinancialHealthAgent output
        assert "forensics_penalty" not in result
        assert "penalty_applied" not in result


# ===========================================================================
# 7. Integration Test — FastAPI Route Layer
# ===========================================================================

class TestForensicsPenaltyRouteIntegration:
    """
    Integration tests for the /api/v1/documents/ingest/pdf endpoint.

    These tests mock out the expensive I/O layers (pikepdf, PDF parsing,
    the LLM) so the test runs without any external dependencies, while
    still exercising the full route-handler path including the penalty
    wiring introduced in AI-A-W4.
    """

    def _make_pdf_bytes(self) -> bytes:
        """
        Return a minimal valid-enough PDF byte string.
        The file will never be opened by pikepdf or PyPDF2 in these tests
        because those calls are mocked — we just need non-empty bytes so the
        route does not reject the upload before reaching our code.
        """
        return b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj\nstartxref\n0\n%%EOF"

    import pytest
    @pytest.fixture(autouse=True)
    def mock_downstream_agents(self, monkeypatch):
        """Mock downstream agents to prevent HITL pauses during integration tests."""
        from unittest.mock import AsyncMock
        
        # Mock ManagementQualityAgent
        mock_mqa = AsyncMock()
        mock_mqa.analyze.return_value = {"management_score": 90, "requires_manual_review": False}
        monkeypatch.setattr("app.agents.orchestration.coordinator.ManagementQualityAgent", lambda: mock_mqa)
        
        # Mock SectorContextAgent
        mock_sca = AsyncMock()
        mock_sca.get_sector_outlook.return_value = {"risk_factors": [], "sector": "Manufacturing"}
        monkeypatch.setattr("app.agents.orchestration.coordinator.SectorContextAgent", lambda: mock_sca)
        
        # Mock IntegrityVerificationAgent
        mock_iva = AsyncMock()
        mock_iva.cross_validate.return_value = {"status": "success", "flags": []}
        monkeypatch.setattr("app.agents.orchestration.coordinator.IntegrityVerificationAgent", lambda: mock_iva)

    def test_suspicious_document_reduces_base_score_by_15(self, client, tmp_path, monkeypatch):
        """
        End-to-end: when run_pdf_forensics returns is_suspicious=True and
        parse_financial_statement returns base_score=80, the response must
        contain base_score=65 and forensics_penalty.penalty_applied=True.
        """
        from unittest.mock import AsyncMock, patch

        suspicious_forensics = {
            "is_suspicious": True,
            "flags": ["UNNATURAL_SOURCE: Created via photoshop"],
            "metadata": {"creator": "Photoshop", "producer": "iLovePDF"},
        }
        ai_result = {
            "company_name": "Test Corp",
            "sector": "Manufacturing",
            "total_revenue": None,
            "total_debt": None,
            "shareholder_equity": None,
            "current_assets": None,
            "current_liabilities": None,
            "base_score": 80,
            "qualitative_notes": "Test",
            "financial_commitments": [],
            "legal_risks": [],
            "sanction_details": [],
        }

        with patch("app.routes.documents.run_pdf_forensics", return_value=suspicious_forensics), \
             patch("app.routes.documents.agent") as mock_agent:
            mock_agent.ingest_pdf = AsyncMock(return_value={"text": "revenue loan balance sheet", "tables_count": 0})
            mock_agent.parse_financial_statement = AsyncMock(return_value=ai_result.copy())

            pdf_bytes = self._make_pdf_bytes()
            response = client.post(
                "/api/v1/documents/ingest/pdf",
                files={"file": ("test_report.pdf", pdf_bytes, "application/pdf")},
            )

        assert response.status_code == 200
        body = response.json()

        # Penalty was applied
        assert body["forensics_penalty"]["penalty_applied"] is True
        assert body["forensics_penalty"]["penalty_points"] == 15
        assert body["forensics_penalty"]["original_score"] == 80
        assert body["forensics_penalty"]["adjusted_score"] == 65

        # base_score in ai_analysis must reflect the adjusted value
        assert body["ai_analysis"]["base_score"] == 65

        # forensics key must still be present (schema unchanged)
        assert "forensics" in body
        assert body["forensics"]["is_suspicious"] is True

    def test_clean_document_score_unchanged(self, client, monkeypatch):
        """
        When is_suspicious=False the base_score must not change at all.
        """
        from unittest.mock import AsyncMock, patch

        clean_forensics = {
            "is_suspicious": False,
            "flags": [],
            "metadata": {"creator": "Microsoft Word", "producer": "Microsoft Word"},
        }
        ai_result = {
            "company_name": "Clean Corp",
            "sector": "Tech",
            "total_revenue": None,
            "total_debt": None,
            "shareholder_equity": None,
            "current_assets": None,
            "current_liabilities": None,
            "base_score": 75,
            "qualitative_notes": "",
            "financial_commitments": [],
            "legal_risks": [],
            "sanction_details": [],
        }

        with patch("app.routes.documents.run_pdf_forensics", return_value=clean_forensics), \
             patch("app.routes.documents.agent") as mock_agent:
            mock_agent.ingest_pdf = AsyncMock(return_value={"text": "revenue loan balance sheet", "tables_count": 0})
            mock_agent.parse_financial_statement = AsyncMock(return_value=ai_result.copy())

            pdf_bytes = self._make_pdf_bytes()
            response = client.post(
                "/api/v1/documents/ingest/pdf",
                files={"file": ("clean_report.pdf", pdf_bytes, "application/pdf")},
            )

        assert response.status_code == 200
        body = response.json()

        # No penalty
        assert body["forensics_penalty"]["penalty_applied"] is False
        assert body["forensics_penalty"]["penalty_points"] == 0
        assert body["forensics_penalty"]["adjusted_score"] == 75

        # base_score unchanged
        assert body["ai_analysis"]["base_score"] == 75

    def test_response_schema_contains_all_existing_keys(self, client):
        """
        All keys that existed in the API response BEFORE AI-A-W4 must still
        be present. forensics_penalty is additive — nothing was removed.
        """
        from unittest.mock import AsyncMock, patch

        forensics = {"is_suspicious": False, "flags": [], "metadata": {}}
        ai_result = {
            "company_name": "Schema Test Co",
            "sector": "Finance",
            "total_revenue": None,
            "total_debt": None,
            "shareholder_equity": None,
            "current_assets": None,
            "current_liabilities": None,
            "base_score": 70,
            "qualitative_notes": "",
            "financial_commitments": [],
            "legal_risks": [],
            "sanction_details": [],
        }

        with patch("app.routes.documents.run_pdf_forensics", return_value=forensics), \
             patch("app.routes.documents.agent") as mock_agent:
            mock_agent.ingest_pdf = AsyncMock(return_value={"text": "revenue loan balance sheet", "tables_count": 0})
            mock_agent.parse_financial_statement = AsyncMock(return_value=ai_result.copy())

            pdf_bytes = self._make_pdf_bytes()
            response = client.post(
                "/api/v1/documents/ingest/pdf",
                files={"file": ("schema_test.pdf", pdf_bytes, "application/pdf")},
            )

        assert response.status_code == 200
        body = response.json()

        # All pre-existing top-level keys must be present
        existing_keys = {"status", "appraisal_id", "filename", "tables_found", "forensics", "ai_analysis"}
        for key in existing_keys:
            assert key in body, f"Pre-existing key '{key}' missing from response"

        # New key introduced by AI-A-W4
        assert "forensics_penalty" in body

    def test_score_floor_at_zero_via_route(self, client):
        """
        Integration-level: base_score=5 with is_suspicious=True must produce
        adjusted_score=0 in the response, never negative.
        """
        from unittest.mock import AsyncMock, patch

        forensics = {"is_suspicious": True, "flags": ["TAMPER"], "metadata": {}}
        ai_result = {
            "company_name": "Low Score Co",
            "sector": "Retail",
            "total_revenue": None, "total_debt": None,
            "shareholder_equity": None, "current_assets": None,
            "current_liabilities": None,
            "base_score": 5,
            "qualitative_notes": "",
            "financial_commitments": [],
            "legal_risks": [],
            "sanction_details": [],
        }

        with patch("app.routes.documents.run_pdf_forensics", return_value=forensics), \
             patch("app.routes.documents.agent") as mock_agent:
            mock_agent.ingest_pdf = AsyncMock(return_value={"text": "revenue loan balance sheet", "tables_count": 0})
            mock_agent.parse_financial_statement = AsyncMock(return_value=ai_result.copy())

            pdf_bytes = self._make_pdf_bytes()
            response = client.post(
                "/api/v1/documents/ingest/pdf",
                files={"file": ("low_score.pdf", pdf_bytes, "application/pdf")},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ai_analysis"]["base_score"] == 0
        assert body["forensics_penalty"]["adjusted_score"] == 0
        assert body["forensics_penalty"]["adjusted_score"] >= 0
