# =============================================================================
# CREDENT — Integration Tests: Traceability & Dynamic Policy Thresholds
# Linear: ASE-42 [QA-W5]
# =============================================================================
"""
Covers ASE-42's three key deliverables:
    1. A simulated frontend upload payload flows through the coordinator with
       zero mapping errors.
    2. "Strict Bank" (cutoff=70) vs "Lenient Bank" (cutoff=50) policy
       simulation — this is meant to prove the dynamic policy engine works.
    3. Citation objects (page/snippet) are present in the final response.

IMPORTANT — READ BEFORE TREATING FAILURES AS YOUR OWN BUG:
Test class TestDynamicPolicyEngine is EXPECTED TO FAIL right now. This is not
a mistake in the test — it's the same bug found during retroactive QA on
ASE-43 (Aug 1 report): coordinator.py fetches the policy from the DB, but
only passes it into build_evidence_trail() (cosmetic ratio labels) — it is
never passed to cam_agent.generate_cam(), which makes the actual decision.
Confirmed by inspecting generate_cam()'s signature directly: it accepts no
policy parameter, and "Score < 60 -> REJECT" is hardcoded in its prompt text.

These tests are written to assert the CORRECT, intended behavior (per ASE-42's
own acceptance criteria). They will start passing once ASE-43 is properly
fixed to route the policy into generate_cam(). Until then, expect:
    test_strict_bank_rejects_document_lenient_bank_approves  -> FAIL
This is intentional and documents the real gap, rather than hiding it behind
tests written to match the current (broken) behavior.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.agents.orchestration.coordinator import AgentCoordinator
from app.database.database import save_policy


def _mocked_coordinator(financial_score: float = 65.0):
    """Builds a coordinator with realistic mocked sub-agent responses,
    including citation data on the ingestion step (for traceability tests)."""
    coordinator = AgentCoordinator()

    coordinator.ingestion_agent = MagicMock()
    coordinator.ingestion_agent.ingest_pdf = AsyncMock(
        return_value={"text": "Sample extracted financial statement text." * 5}
    )
    coordinator.ingestion_agent.parse_financial_statement = AsyncMock(
        return_value={
            "company_name": "Traceability Test Co",
            "sector": "Manufacturing",
            "citations": {
                "revenue": [{"page": 2, "snippet": "Total Revenue: INR 10,000,000"}],
                "total_debt": [{"page": 3, "snippet": "Total Debt: INR 5,000,000"}],
                "shareholder_equity": [{"page": 3, "snippet": "Total Equity: INR 8,000,000"}],
            },
        }
    )

    coordinator.financial_agent = MagicMock()
    coordinator.financial_agent.analyze = AsyncMock(
        return_value={"status": "success", "financial_health_score": financial_score}
    )

    coordinator.management_agent = MagicMock()
    coordinator.management_agent.analyze = AsyncMock(
        return_value={"status": "success", "management_score": 75.0}
    )

    coordinator.sector_agent = MagicMock()
    coordinator.sector_agent.get_sector_outlook = AsyncMock(
        return_value={"status": "success", "outlook": "Stable", "risk_factors": []}
    )
    coordinator.sector_agent.check_rbi_policies = AsyncMock(return_value=[])

    coordinator.integrity_agent = MagicMock()
    coordinator.integrity_agent.cross_validate = AsyncMock(
        return_value={"status": "completed", "flags": [], "warnings": []}
    )

    coordinator.cam_agent = MagicMock()
    coordinator.cam_agent.generate_cam = AsyncMock(
        return_value={
            "five_cs": {},
            "decision": "APPROVE",
            "decision_rationale": "Financials support approval.",
            "recommended_loan_amount": "INR 20,00,000",
            "recommended_interest_rate": "13%",
        }
    )

    return coordinator


# ---------------------------------------------------------------------------
# 1. Frontend upload payload -> zero mapping errors
# ---------------------------------------------------------------------------

class TestFrontendUploadPayloadMapping:

    @pytest.mark.asyncio
    async def test_simulated_frontend_payload_produces_zero_mapping_errors(self):
        """A realistic frontend-shaped payload should flow through run_appraisal
        without KeyError/AttributeError/mapping exceptions."""
        coordinator = _mocked_coordinator()
        frontend_payload = {
            "file_path": "uploaded_statement.pdf",
            "institution_id": "DEFAULT",
            "promoter_ids": [],
            "gst_data": [],
            "bank_data": [],
        }

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal(frontend_payload)

        assert result["status"] == "success"
        assert "individual_agent_outputs" in result
        assert "combined_decision" in result
        # No raw exception objects leaking into the response
        for key, value in result["individual_agent_outputs"].items():
            assert not isinstance(value, Exception), f"{key} output leaked a raw exception"


# ---------------------------------------------------------------------------
# 2. Dynamic Policy Engine — Strict Bank vs Lenient Bank
# ---------------------------------------------------------------------------

class TestDynamicPolicyEngine:
    """
    EXPECTED TO FAIL as of Aug 1, 2026 — documents a real, confirmed bug.
    See module docstring above for full explanation.
    """

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="Confirmed bug (Aug 1 QA report): coordinator.py fetches the DB "
                "policy but never passes it to cam_agent.generate_cam() — only to "
                "build_evidence_trail(). Remove this xfail once ASE-43 is fixed to "
                "route the policy into generate_cam().",
        strict=True,
    )
    async def test_strict_bank_rejects_document_lenient_bank_approves(self):
        """
        Same document (financial_health_score=65) evaluated under two
        different institution policies:
          - "Strict Bank": auto_approve_cutoff = 70 -> score 65 is BELOW
            cutoff, should NOT auto-approve (expect REJECT or MANUAL REVIEW)
          - "Lenient Bank": auto_approve_cutoff = 50 -> score 65 is ABOVE
            cutoff, should be eligible for APPROVE

        This is the core acceptance criteria for ASE-42 and ASE-43 alike:
        the SAME document must produce DIFFERENT decisions purely based on
        DB policy, with no code changes.
        """
        base_policy_fields = {
            "current_ratio_safe": 1.2, "current_ratio_min": 1.0,
            "dscr_safe": 1.25, "dscr_min": 1.0, "de_high": 2.0,
            "penalty_weights": {"integrity_mismatch": 15.0, "promoter_flags": 10.0},
        }

        # Strict Bank
        save_policy({"institution_id": "STRICT_BANK", "auto_approve_cutoff": 70.0,
                     "auto_reject_cutoff": 40.0, **base_policy_fields})
        strict_coordinator = _mocked_coordinator(financial_score=65.0)
        with patch("os.path.exists", return_value=True):
            strict_result = await strict_coordinator.run_appraisal(
                {"file_path": "fake.pdf", "institution_id": "STRICT_BANK"}
            )

        # Lenient Bank
        save_policy({"institution_id": "LENIENT_BANK", "auto_approve_cutoff": 50.0,
                     "auto_reject_cutoff": 30.0, **base_policy_fields})
        lenient_coordinator = _mocked_coordinator(financial_score=65.0)
        with patch("os.path.exists", return_value=True):
            lenient_result = await lenient_coordinator.run_appraisal(
                {"file_path": "fake.pdf", "institution_id": "LENIENT_BANK"}
            )

        strict_decision = strict_result["combined_decision"]["decision"]
        lenient_decision = lenient_result["combined_decision"]["decision"]

        # THE ACTUAL BUG: as of Aug 1 2026, both come back identical (APPROVE)
        # because the policy never reaches generate_cam(). This assertion
        # documents the intended, correct behavior.
        assert strict_decision != lenient_decision, (
            f"Same document scored identically regardless of bank policy "
            f"(strict={strict_decision}, lenient={lenient_decision}). "
            f"The DB policy is not reaching the CAM decision logic — "
            f"see coordinator.py, generate_cam() is never passed `policy`."
        )


# ---------------------------------------------------------------------------
# 3. Citation objects present in the final response
# ---------------------------------------------------------------------------

class TestCitationTraceability:

    @pytest.mark.asyncio
    async def test_citation_objects_present_in_final_response(self):
        """Confirms citation metadata (page/snippet) from ingestion survives
        all the way through to the final coordinator response."""
        coordinator = _mocked_coordinator()

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        ingestion_output = result["individual_agent_outputs"].get("ingestion", {})
        citations = ingestion_output.get("citations")

        assert citations is not None, "citations object missing from final response"
        assert "revenue" in citations
        assert citations["revenue"][0]["page"] == 2
        assert "snippet" in citations["revenue"][0]

    @pytest.mark.asyncio
    async def test_missing_citations_does_not_crash_pipeline(self):
        """If the AI fails to produce citations for a given document, the
        pipeline should degrade gracefully, not crash."""
        coordinator = _mocked_coordinator()
        coordinator.ingestion_agent.parse_financial_statement = AsyncMock(
            return_value={"company_name": "No Citation Co", "sector": "Retail"}
            # no "citations" key at all
        )

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["status"] == "success"