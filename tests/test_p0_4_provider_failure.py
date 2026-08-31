"""P0-4 - a provider failure must never surface as an underwriting conclusion.

The production incident this pins down: the Sarvam account ran out of credits
and every LLM call returned HTTP 402 insufficient_quota_error. Each agent caught
it and returned placeholders, so the appraisal completed with HTTP 200 and the
UI displayed:

    Borrower: Unknown Entity | Revenue: N/A | Key Concerns: SYSTEM ERROR
    System Decision: MANUAL REVIEW REQUIRED

MANUAL REVIEW is a human underwriting conclusion. A credit officer cannot tell
it apart from a real one. The correct state for a total provider failure is
ANALYSIS_INCOMPLETE with decision_allowed=False.

The gate already existed and worked - it was simply never applied to
POST /reports/generate-cam.
"""

import pytest

from app.core.appraisal_safety import gate_cam_response
from app.core.execution_state import DECISION_ANALYSIS_INCOMPLETE


# The literal payloads the failure produced, taken from the source of each
# fallback rather than invented.
DEFAULT_EXTRACTION_AFTER_402 = {
    "extraction_degraded": True,
    "degradation_reason": "AI extraction did not complete.",
    "company_name": "Unknown Entity",
    "sector": "Unknown",
    "total_revenue": None,
    "total_debt": None,
    "shareholder_equity": None,
    "base_score": 65,
}

CAM_FALLBACK_AFTER_402 = {
    "document_control": {"borrower_name": "Unknown Entity", "status": "ERROR"},
    "executive_summary": {
        "industry": "UNKNOWN", "revenue": "N/A", "ebitda": "N/A", "pat": "N/A",
        "strengths": [], "key_concerns": ["SYSTEM ERROR"], "critical_conditions": [],
    },
    "five_cs": {"character": "N/A", "capacity": "N/A", "capital": "N/A",
                "collateral": "N/A", "conditions": "N/A"},
    "recommendation": {"decision": "MANUAL REVIEW",
                       "rationale": "System error during CAM generation."},
    "decision": "MANUAL REVIEW",
    "recommended_loan_amount": "Withheld",
    "recommended_interest_rate": "TBD",
}

HEALTHY_CAM_MANUAL_REVIEW = {
    "document_control": {"borrower_name": "Meridian Auto Components", "status": "COMPLETE"},
    "executive_summary": {
        "industry": "Auto components", "revenue": "38 Cr", "ebitda": "4.2 Cr",
        "pat": "1.9 Cr", "strengths": ["Stable order book"],
        "key_concerns": ["Customer concentration"], "critical_conditions": [],
    },
    "five_cs": {
        "character": {"evidence": "Promoter clean", "assessment": "Satisfactory",
                      "risk_implication": "Low"},
        "capacity": {"evidence": "DSCR 1.4", "assessment": "Adequate",
                     "risk_implication": "Moderate"},
        "capital": {"evidence": "D/E 1.1", "assessment": "Acceptable",
                    "risk_implication": "Moderate"},
        "collateral": {"evidence": "Property charge", "assessment": "Adequate",
                       "risk_implication": "Low"},
        "conditions": {"evidence": "Sector stable", "assessment": "Neutral",
                       "risk_implication": "Moderate"},
    },
    "recommendation": {"decision": "MANUAL REVIEW",
                       "rationale": "Customer concentration warrants a human decision."},
    "decision": "MANUAL REVIEW",
    "recommended_loan_amount": "30 Cr",
    "recommended_interest_rate": "11.5%",
}

HEALTHY_EXTRACTION = {
    "extraction_degraded": False,
    "company_name": "Meridian Auto Components",
    "sector": "Auto components",
    "total_revenue": 380000000,
    "total_debt": 120000000,
    "shareholder_equity": 110000000,
    "base_score": 72,
}


# ---------------------------------------------------------------------------
# The production incident
# ---------------------------------------------------------------------------

class TestTotalProviderFailure:
    def test_402_cascade_is_analysis_incomplete_not_manual_review(self):
        """The exact payloads the 402 outage produced."""
        result, gate = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)

        assert result["decision"] == DECISION_ANALYSIS_INCOMPLETE
        assert result["decision"] != "MANUAL REVIEW"
        assert gate["decision_allowed"] is False
        assert gate["analysis_status"] == "FAILED"

    def test_decision_allowed_is_false(self):
        _, gate = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        assert gate["decision_allowed"] is False

    def test_the_gate_fields_travel_to_the_client(self):
        """The frontend already renders these; it just never received them."""
        result, _ = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        for field in ("analysis_status", "decision_allowed", "missing_required",
                      "degraded_components"):
            assert field in result, f"{field} must reach the client"

    def test_both_failed_components_are_named(self):
        _, gate = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        assert "document_ingestion" in gate["missing_required"]
        assert "cam_generator" in gate["missing_required"]

    def test_no_credit_figures_are_offered(self):
        result, _ = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        assert result["recommended_loan_amount"] == "UNAVAILABLE"
        assert result["recommended_interest_rate"] == "UNAVAILABLE"
        assert result["recommended_loan_amount"] != "Withheld"

    def test_rationale_says_system_failure_not_underwriting(self):
        result, _ = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        assert "system failure" in result["decision_rationale"].lower()
        assert "not an underwriting conclusion" in result["decision_rationale"].lower()

    def test_nested_recommendation_is_also_corrected(self):
        """A client reading recommendation.decision must not see MANUAL REVIEW."""
        result, _ = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        assert result["recommendation"]["decision"] == DECISION_ANALYSIS_INCOMPLETE

    def test_the_caller_payload_is_not_mutated(self):
        original = dict(CAM_FALLBACK_AFTER_402)
        gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, CAM_FALLBACK_AFTER_402)
        assert CAM_FALLBACK_AFTER_402 == original


# ---------------------------------------------------------------------------
# Placeholder values must never be presented as a successful appraisal
# ---------------------------------------------------------------------------

class TestPlaceholdersAreNeverAnAppraisal:
    def test_unknown_entity_extraction_blocks_the_decision(self):
        _, gate = gate_cam_response(DEFAULT_EXTRACTION_AFTER_402, HEALTHY_CAM_MANUAL_REVIEW)
        assert gate["decision_allowed"] is False, (
            "a CAM built on placeholder extraction is not a valid appraisal"
        )

    def test_cam_error_status_blocks_the_decision(self):
        _, gate = gate_cam_response(HEALTHY_EXTRACTION, CAM_FALLBACK_AFTER_402)
        assert gate["decision_allowed"] is False
        assert "cam_generator" in gate["missing_required"]

    def test_five_cs_collapsed_to_placeholders_blocks_the_decision(self):
        cam = dict(HEALTHY_CAM_MANUAL_REVIEW)
        cam["five_cs"] = {k: "N/A" for k in
                          ("character", "capacity", "capital", "collateral", "conditions")}
        cam["document_control"] = {"borrower_name": "X", "status": "COMPLETE"}
        _, gate = gate_cam_response(HEALTHY_EXTRACTION, cam)
        assert gate["decision_allowed"] is False

    def test_empty_payloads_block_the_decision(self):
        result, gate = gate_cam_response({}, {})
        assert gate["decision_allowed"] is False
        assert result["decision"] == DECISION_ANALYSIS_INCOMPLETE


# ---------------------------------------------------------------------------
# A genuine MANUAL REVIEW must survive untouched
# ---------------------------------------------------------------------------

class TestGenuineManualReviewIsPreserved:
    def test_healthy_manual_review_is_not_downgraded(self):
        result, gate = gate_cam_response(HEALTHY_EXTRACTION, HEALTHY_CAM_MANUAL_REVIEW)

        assert gate["decision_allowed"] is True
        assert result["decision"] == "MANUAL REVIEW"
        assert result["decision"] != DECISION_ANALYSIS_INCOMPLETE

    def test_healthy_manual_review_keeps_its_figures(self):
        result, _ = gate_cam_response(HEALTHY_EXTRACTION, HEALTHY_CAM_MANUAL_REVIEW)
        assert result["recommended_loan_amount"] == "30 Cr"
        assert result["recommended_interest_rate"] == "11.5%"

    def test_healthy_manual_review_reports_completed(self):
        _, gate = gate_cam_response(HEALTHY_EXTRACTION, HEALTHY_CAM_MANUAL_REVIEW)
        assert gate["analysis_status"] in ("COMPLETED", "DEGRADED")
        assert gate["missing_required"] == []

    @pytest.mark.parametrize("decision", ["APPROVE", "REJECT", "MANUAL REVIEW"])
    def test_every_genuine_decision_verb_survives(self, decision):
        cam = dict(HEALTHY_CAM_MANUAL_REVIEW)
        cam["decision"] = decision
        result, gate = gate_cam_response(HEALTHY_EXTRACTION, cam)
        assert gate["decision_allowed"] is True
        assert result["decision"] == decision


# ---------------------------------------------------------------------------
# HTTP 402 is terminal, not retryable
# ---------------------------------------------------------------------------

class TestQuotaExhaustionIsNotRetried:
    def test_402_is_classified_as_provider_exhausted(self):
        from app.core.llm import _PROVIDER_EXHAUSTED_STATUS

        assert 402 in _PROVIDER_EXHAUSTED_STATUS

    def test_402_is_not_in_the_retryable_set(self):
        """Unlike 429, an empty account does not refill on its own."""
        from app.core.llm import _RETRYABLE_STATUS, _is_retryable

        assert 402 not in _RETRYABLE_STATUS
        assert _is_retryable(Exception("no credits"), 402) is False

    def test_402_is_not_treated_as_a_move_to_next_model(self):
        """Every model on the provider refuses identically, so rollover is futile."""
        from app.core.llm import _MOVE_ON_STATUS

        assert 402 not in _MOVE_ON_STATUS

    def test_429_remains_retryable(self):
        """The existing transient handling must not be broken by this change."""
        from app.core.llm import _is_retryable

        assert _is_retryable(Exception("rate limit"), 429) is True

    def test_413_still_moves_to_the_next_model(self):
        from app.core.llm import _MOVE_ON_STATUS

        assert 413 in _MOVE_ON_STATUS


# ---------------------------------------------------------------------------
# The endpoint applies the gate on every return path
# ---------------------------------------------------------------------------

class TestGenerateCamEndpointIsGated:
    def test_every_return_path_is_gated(self):
        import inspect

        from app.routes import reports

        source = inspect.getsource(reports.generate_credit_appraisal_memo)
        assert source.count("gate_cam_response") >= 3, (
            "each return path of generate-cam must be gated, including the "
            "exception fallbacks - an ungated one reintroduces the incident"
        )

    def test_the_gate_outcome_is_persisted(self):
        import inspect

        from app.routes import reports

        source = inspect.getsource(reports.generate_credit_appraisal_memo)
        assert '"analysis_status": _gate["analysis_status"]' in source
        assert '"decision_allowed": _gate["decision_allowed"]' in source
