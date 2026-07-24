import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.agents.orchestration.coordinator import AgentCoordinator

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def coordinator_instance():
    """Returns a coordinator instance with mocked sub-agents."""
    coordinator = AgentCoordinator()
    coordinator.ingestion_agent = MagicMock()
    coordinator.financial_agent = MagicMock()
    coordinator.management_agent = MagicMock()
    coordinator.sector_agent = MagicMock()
    coordinator.integrity_agent = MagicMock()
    coordinator.cam_agent = MagicMock()
    return coordinator

# ---------------------------------------------------------------------------
# Coordinator Initialization Tests
# ---------------------------------------------------------------------------

async def test_coordinator_initialization(coordinator_instance):
    """Verify coordinator construction, sub-agent initialization, and LLM setup."""
    assert coordinator_instance.ingestion_agent is not None
    assert coordinator_instance.financial_agent is not None
    assert coordinator_instance.management_agent is not None
    assert coordinator_instance.sector_agent is not None
    assert coordinator_instance.integrity_agent is not None
    assert coordinator_instance.cam_agent is not None
    assert coordinator_instance.llm is not None

# ---------------------------------------------------------------------------
# build_evidence_trail() Tests
# ---------------------------------------------------------------------------

async def test_build_evidence_trail_aggregation(coordinator_instance):
    """Verify successful aggregation of warnings across agent outputs."""
    agent_outputs = {
        "ingestion": {
            "legal_risks": ["Active pending litigation."],
            "sanction_details": ["Limits ₹1Cr"]
        },
        "financial_health": {
            "ratios": {
                "current_ratio": 0.8,
                "dscr": 0.9,
                "debt_to_equity": 2.5
            },
            "analysis_notes": ["Zero cash balance detected."],
            "cash_flow_assessment": {"status": "Weak"}
        },
        "management_quality": {
            "promoter_analysis": [
                {
                    "name": "Promoter A",
                    "risk_flags": ["PAST_DEFAULTS_DETECTED"]
                }
            ]
        },
        "sector_context": {
            "sector": "Real Estate",
            "outlook": "Negative",
            "risk_factors": ["High headwinds expected."],
            "rbi_policy_impact": [
                {
                    "circular_ref": "RBI/2026/01",
                    "summary": "Stricter lending norms",
                    "impact": "Unfavorable"
                }
            ]
        },
        "integrity_check": {
            "flags": [
                {
                    "flag": "High Revenue Discrepancy",
                    "severity": "HIGH",
                    "details": "GST returns deviate from bank credits by 45%"
                }
            ],
            "warnings": ["Missing GST ledger files"]
        }
    }

    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    assert len(trail) > 0
    categories = {item["category"] for item in trail}
    assert "Legal/Compliance" in categories
    assert "Financial Ratios" in categories
    assert "Promoter Risk" in categories
    assert "Sector/Macro Risk" in categories
    assert "Data Integrity" in categories

async def test_build_evidence_trail_duplicate_removal(coordinator_instance):
    """Verify identical warnings from same agent are de-duplicated."""
    agent_outputs = {
        "ingestion": {
            "legal_risks": ["Active litigation", "Active litigation"]
        }
    }
    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    descriptions = [item["description"] for item in trail if item["category"] == "Legal/Compliance"]
    assert len(descriptions) == 1
    assert descriptions[0] == "Active litigation"

async def test_build_evidence_trail_deterministic_ordering(coordinator_instance):
    """Verify that evidence aggregation order remains deterministic."""
    agent_outputs = {
        "ingestion": {"legal_risks": ["Dispute A"]},
        "financial_health": {"analysis_notes": ["Dispute B"]}
    }
    trail1 = await coordinator_instance.build_evidence_trail(agent_outputs)
    trail2 = await coordinator_instance.build_evidence_trail(agent_outputs)
    assert trail1 == trail2

async def test_build_evidence_trail_empty_inputs(coordinator_instance):
    """Verify that empty inputs are handled cleanly without exceptions."""
    trail = await coordinator_instance.build_evidence_trail({})
    assert trail == []

async def test_build_evidence_trail_malformed_inputs(coordinator_instance):
    """Verify malformed input types degrade to empty trail gracefully."""
    trail = await coordinator_instance.build_evidence_trail("malformed_payload")
    assert trail == []

async def test_build_evidence_trail_missing_keys(coordinator_instance):
    """Verify missing agent sub-keys are skipped safely."""
    agent_outputs = {
        "ingestion": {"legal_risks": ["Active dispute"]}
    }
    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    assert len(trail) == 1
    assert trail[0]["category"] == "Legal/Compliance"

# ---------------------------------------------------------------------------
# generate_explanation() Tests
# ---------------------------------------------------------------------------

async def test_generate_explanation_success(coordinator_instance):
    """Verify successful LLM rationale synthesis."""
    mock_response = MagicMock()
    mock_response.content = "Appraisal Rationale Summary: Strong credit profiles."
    
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", AsyncMock(return_value=mock_response)):
        evidence = [
            {"category": "Financial Ratios", "source_agent": "FinAgent", "severity": "INFO", "title": "Good Ratios", "description": "Safe current ratio", "recommendation": "None"}
        ]
        explanation = await coordinator_instance.generate_explanation(evidence)
        assert explanation == "Appraisal Rationale Summary: Strong credit profiles."

async def test_generate_explanation_timeout_fallback(coordinator_instance):
    """Verify local deterministic fallback is used during LLM timeouts."""
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        evidence = [
            {"category": "Financial Ratios", "source_agent": "FinancialHealthAgent", "severity": "HIGH", "title": "Low Current Ratio", "description": "0.8 ratio detected.", "recommendation": "Review working capital"}
        ]
        explanation = await coordinator_instance.generate_explanation(evidence)
        assert "Appraisal Summary (System Fallback):" in explanation
        assert "Negative/Warning Factors:" in explanation
        assert "Low Current Ratio: 0.8 ratio detected." in explanation

async def test_generate_explanation_exception_fallback(coordinator_instance):
    """Verify local deterministic fallback is used during LLM general API exceptions."""
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", side_effect=RuntimeError("Groq Connection Error")):
        evidence = [
            {"category": "Promoter Risk", "source_agent": "ManagementQualityAgent", "severity": "CRITICAL", "title": "Defaults Detected", "description": "Promoter default history", "recommendation": "CIBIL check required"}
        ]
        explanation = await coordinator_instance.generate_explanation(evidence)
        assert "Appraisal Summary (System Fallback):" in explanation
        assert "Defaults Detected: Promoter default history" in explanation

async def test_generate_explanation_empty_evidence(coordinator_instance):
    """Verify empty evidence list immediately returns manual review recommendation."""
    explanation = await coordinator_instance.generate_explanation([])
    assert "No credit appraisal evidence was provided" in explanation


# =============================================================================
# ASE-36 [QA-W4] — run_appraisal() Integration Tests
# Added by: Sarvesh Gajakosh (QA)
# =============================================================================
"""
The tests above (by Shlok) cover build_evidence_trail() and
generate_explanation() thoroughly. This section adds coverage for
run_appraisal() itself — the actual multi-agent dispatch pipeline — which
was the core acceptance criteria for ASE-36: "verify that all sub-agents
are called during run_appraisal."

Requires GROQ_API_KEY to be set (even a dummy value) in the environment,
since CAMGeneratorAgent's constructor calls ChatGroq() with no fallback
default and will raise on import otherwise — see flagged issue in PR notes.
"""

import os
from datetime import datetime


def _mocked_coordinator():
    """Builds an AgentCoordinator with every sub-agent replaced by AsyncMocks
    returning healthy, realistic default responses. Individual tests override
    only the specific mock they care about."""
    coordinator = AgentCoordinator()

    coordinator.ingestion_agent = MagicMock()
    coordinator.ingestion_agent.ingest_pdf = AsyncMock(
        return_value={"text": "Sample extracted financial statement text." * 3}
    )
    coordinator.ingestion_agent.parse_financial_statement = AsyncMock(
        return_value={"company_name": "Integration Test Co", "sector": "Manufacturing"}
    )

    coordinator.financial_agent = MagicMock()
    coordinator.financial_agent.analyze = AsyncMock(
        return_value={"status": "success", "financial_health_score": 80.0}
    )

    coordinator.management_agent = MagicMock()
    coordinator.management_agent.analyze = AsyncMock(
        return_value={"status": "success", "management_score": 75.0}
    )

    coordinator.sector_agent = MagicMock()
    coordinator.sector_agent.get_sector_outlook = AsyncMock(
        return_value={"status": "success", "sector": "Manufacturing", "outlook": "Stable", "risk_factors": []}
    )
    coordinator.sector_agent.check_rbi_policies = AsyncMock(return_value=[])

    coordinator.integrity_agent = MagicMock()
    coordinator.integrity_agent.cross_validate = AsyncMock(
        return_value={"status": "completed", "flags": [], "warnings": []}
    )

    coordinator.cam_agent = MagicMock()
    coordinator.cam_agent.generate_cam = AsyncMock(
        return_value={
            "five_cs": {"character": "Good", "capacity": "Good", "capital": "Good", "collateral": "Good", "conditions": "Good"},
            "decision": "APPROVE",
            "recommended_loan_amount": "INR 50,00,000",
            "recommended_interest_rate": "12.5%",
            "decision_rationale": "Strong financials across all metrics.",
        }
    )

    return coordinator


class TestRunAppraisalInputValidation:

    async def test_non_dict_application_data_raises_value_error(self):
        coordinator = _mocked_coordinator()
        with pytest.raises(ValueError, match="must be a dictionary"):
            await coordinator.run_appraisal("not a dict")

    async def test_missing_file_path_raises_value_error(self):
        coordinator = _mocked_coordinator()
        with pytest.raises(ValueError, match="file_path"):
            await coordinator.run_appraisal({})

    async def test_nonexistent_file_path_raises_value_error(self):
        coordinator = _mocked_coordinator()
        with patch("os.path.exists", return_value=False):
            with pytest.raises(ValueError, match="File not found"):
                await coordinator.run_appraisal({"file_path": "does_not_exist.pdf"})


class TestRunAppraisalDispatch:
    """Core ASE-36 acceptance criteria: all sub-agents must be called."""

    async def test_all_five_sub_agents_are_called(self):
        coordinator = _mocked_coordinator()
        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        coordinator.ingestion_agent.ingest_pdf.assert_called_once()
        coordinator.ingestion_agent.parse_financial_statement.assert_called_once()
        coordinator.financial_agent.analyze.assert_called_once()
        coordinator.management_agent.analyze.assert_called_once()
        coordinator.sector_agent.get_sector_outlook.assert_called_once()
        coordinator.sector_agent.check_rbi_policies.assert_called_once()
        coordinator.integrity_agent.cross_validate.assert_called_once()
        coordinator.cam_agent.generate_cam.assert_called_once()

        assert result["status"] == "success"
        assert set(result["individual_agent_outputs"].keys()) == {
            "ingestion", "financial_health", "management_quality", "sector_context", "integrity_check"
        }

    async def test_result_contains_appraisal_id_evidence_and_explanation(self):
        coordinator = _mocked_coordinator()
        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["appraisal_id"].startswith("APPRAISAL_")
        assert "evidence_trail" in result
        assert "explanation" in result
        assert "combined_decision" in result
        assert result["combined_decision"]["decision"] == "APPROVE"

    async def test_ingestion_failure_is_fail_fast_not_graceful(self):
        """
        Unlike the four downstream analysis agents, ingestion is NOT wrapped in
        a fallback — a failed ingestion aborts the whole appraisal immediately.
        This is intentional (no financial data = nothing to appraise), but
        worth locking in with a test so it can't silently change later.
        """
        coordinator = _mocked_coordinator()
        coordinator.ingestion_agent.ingest_pdf = AsyncMock(return_value={"error": "Corrupted PDF"})

        with patch("os.path.exists", return_value=True):
            with pytest.raises(ValueError, match="Ingestion"):
                await coordinator.run_appraisal({"file_path": "fake.pdf"})


class TestRunAppraisalGracefulDegradation:
    """
    Downstream analysis agents (financial/management/sector/integrity) are
    dispatched via asyncio.gather(..., return_exceptions=True) — one agent
    failing should NOT crash the whole pipeline, it should fall back to
    documented default values instead.
    """

    async def test_financial_agent_failure_falls_back_gracefully(self):
        coordinator = _mocked_coordinator()
        coordinator.financial_agent.analyze = AsyncMock(side_effect=Exception("Simulated LLM token limit exceeded"))

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["status"] == "success"
        fin_output = result["individual_agent_outputs"]["financial_health"]
        assert fin_output["financial_health_score"] == 50.0
        assert "defaulted due to agent failure" in fin_output["analysis_notes"][0]

    async def test_management_agent_failure_falls_back_gracefully(self):
        coordinator = _mocked_coordinator()
        coordinator.management_agent.analyze = AsyncMock(side_effect=Exception("Simulated timeout"))

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["status"] == "success"
        mgt_output = result["individual_agent_outputs"]["management_quality"]
        assert mgt_output["management_score"] == 0.0
        assert mgt_output["risk_level"] == "Undetermined"

    async def test_all_four_downstream_agents_failing_still_returns_success(self):
        """Worst case: every downstream analysis agent fails. Pipeline should
        still complete via fallbacks, not crash entirely."""
        coordinator = _mocked_coordinator()
        coordinator.financial_agent.analyze = AsyncMock(side_effect=Exception("fail"))
        coordinator.management_agent.analyze = AsyncMock(side_effect=Exception("fail"))
        coordinator.sector_agent.get_sector_outlook = AsyncMock(side_effect=Exception("fail"))
        coordinator.integrity_agent.cross_validate = AsyncMock(side_effect=Exception("fail"))

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["status"] == "success"
        assert result["combined_decision"] is not None


class TestRunAppraisalCamFallback:
    """
    Updated after Tanisha's ASE-34 fallback fix (PR #25): coordinator.py now
    consistently returns MANUAL REVIEW on a CAM Generator failure, matching
    cam_generator.py's own internal handler. Previously these two layers
    contradicted each other (flagged by QA) — this is now fixed and locked in.
    """

    async def test_cam_failure_defaults_to_manual_review_regardless_of_score(self):
        coordinator = _mocked_coordinator()
        coordinator.financial_agent.analyze = AsyncMock(
            return_value={"status": "success", "financial_health_score": 80.0}
        )
        coordinator.cam_agent.generate_cam = AsyncMock(side_effect=Exception("Simulated CAM LLM failure"))

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["combined_decision"]["decision"] == "MANUAL REVIEW"

    async def test_cam_failure_with_low_score_also_defaults_to_manual_review(self):
        coordinator = _mocked_coordinator()
        coordinator.financial_agent.analyze = AsyncMock(
            return_value={"status": "success", "financial_health_score": 40.0}
        )
        coordinator.cam_agent.generate_cam = AsyncMock(side_effect=Exception("Simulated CAM LLM failure"))

        with patch("os.path.exists", return_value=True):
            result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

        assert result["combined_decision"]["decision"] == "MANUAL REVIEW"