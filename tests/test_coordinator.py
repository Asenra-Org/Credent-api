# =============================================================================
# CREDENT — Coordinator Test Suite
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.agents.orchestration.coordinator import AgentCoordinator

@pytest.fixture
def coordinator_instance():
    """Fixture to provide a clean AgentCoordinator instance for testing."""
    return AgentCoordinator()

def mock_wait_for_with_cleanup(return_value=None, side_effect=None):
    """Custom mock for asyncio.wait_for that properly closes/cancels unawaited coroutines to prevent resource leaks."""
    async def _mock_wait_for(fut, timeout=None):
        try:
            if side_effect:
                if isinstance(side_effect, Exception):
                    raise side_effect
                elif callable(side_effect):
                    res = side_effect()
                    if asyncio.iscoroutine(res):
                        await res
                    return res
                else:
                    raise side_effect
            return return_value
        finally:
            if asyncio.iscoroutine(fut):
                fut.close()
            elif isinstance(fut, asyncio.Future):
                fut.cancel()

    return _mock_wait_for

# ---------------------------------------------------------------------------
# Initialization & Setup Tests
# ---------------------------------------------------------------------------

def test_coordinator_initialization(coordinator_instance):
    """Verify that all required sub-agents and LLMs are initialized."""
    assert coordinator_instance.ingestion_agent is not None
    assert coordinator_instance.financial_agent is not None
    assert coordinator_instance.management_agent is not None
    assert coordinator_instance.sector_agent is not None
    assert coordinator_instance.integrity_agent is not None
    assert coordinator_instance.cam_agent is not None
    assert coordinator_instance.llm is not None

# ---------------------------------------------------------------------------
# build_evidence_trail() Aggregation & Severity Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_evidence_trail_aggregation(coordinator_instance):
    """Verify evidence aggregation across financial, integrity, and management agents."""
    agent_outputs = {
        "financial_health": {
            "ratios": {
                "current_ratio": 0.8,  # Triggers HIGH severity warning
                "dscr": 1.1            # Triggers MEDIUM severity warning
            }
        },
        "integrity_check": {
            "flags": [
                {
                    "flag": "Circular Trading Flag",
                    "severity": "CRITICAL",
                    "details": "Suspicious circular flow identified with vendor X."
                }
            ]
        },
        "management_quality": {
            "promoter_analysis": [
                {
                    "name": "Promoter A",
                    "risk_flags": ["Wilful Defaulter History"]
                }
            ]
        }
    }

    trail = await coordinator_instance.build_evidence_trail(agent_outputs)

    assert len(trail) >= 3
    categories = [item["category"] for item in trail]
    assert "Financial Ratios" in categories
    assert "Data Integrity" in categories
    assert "Promoter Risk" in categories

    severities = [item["severity"] for item in trail]
    assert "HIGH" in severities
    assert "CRITICAL" in severities

@pytest.mark.asyncio
async def test_build_evidence_trail_duplicate_removal(coordinator_instance):
    """Verify that identical evidence findings are deduplicated."""
    agent_outputs = {
        "integrity_check": {
            "flags": [
                {"flag": "GST Mismatch", "severity": "HIGH", "details": "10% turnover mismatch"},
                {"flag": "GST Mismatch", "severity": "HIGH", "details": "10% turnover mismatch"}  # Duplicate
            ]
        }
    }

    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    gst_items = [i for i in trail if i["title"] == "GST Mismatch"]
    assert len(gst_items) == 1

@pytest.mark.asyncio
async def test_build_evidence_trail_deterministic_ordering(coordinator_instance):
    """Verify that evidence items are collected from all sub-agents deterministically."""
    agent_outputs = {
        "financial_health": {"ratios": {"current_ratio": 0.9}}, # HIGH
        "integrity_check": {
            "flags": [
                {"flag": "Tax Default", "severity": "CRITICAL", "details": "Unpaid GST liabilities"}
            ]
        }
    }

    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    assert len(trail) == 2
    assert trail[0]["category"] == "Financial Ratios"
    assert trail[1]["category"] == "Data Integrity"
    assert trail[1]["severity"] == "CRITICAL"

@pytest.mark.asyncio
async def test_build_evidence_trail_empty_inputs(coordinator_instance):
    """Verify clean empty list output when agent outputs contain no warnings."""
    trail = await coordinator_instance.build_evidence_trail({})
    assert trail == []

@pytest.mark.asyncio
async def test_build_evidence_trail_malformed_inputs(coordinator_instance):
    """Verify resilience against non-dict or None outputs from agents."""
    agent_outputs = {
        "financial_health": None,
        "integrity_check": "Invalid String Output",
        "management_quality": 12345
    }

    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    assert isinstance(trail, list)
    assert len(trail) == 0

@pytest.mark.asyncio
async def test_build_evidence_trail_missing_keys(coordinator_instance):
    """Verify graceful handling when dictionary keys are missing."""
    agent_outputs = {
        "financial_health": {"ratios": {}},
        "integrity_check": {},
        "management_quality": {}
    }

    trail = await coordinator_instance.build_evidence_trail(agent_outputs)
    assert isinstance(trail, list)

# ---------------------------------------------------------------------------
# generate_explanation() Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_explanation_success(coordinator_instance):
    """Verify successful LLM rationale synthesis."""
    mock_response = MagicMock()
    mock_response.content = "Appraisal Rationale Summary: Strong credit profiles."
    
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", side_effect=mock_wait_for_with_cleanup(return_value=mock_response)):
        evidence = [
            {"category": "Financial Ratios", "source_agent": "FinAgent", "severity": "INFO", "title": "Good Ratios", "description": "Safe current ratio", "recommendation": "None"}
        ]
        explanation = await coordinator_instance.generate_explanation(evidence)
        assert explanation == "Appraisal Rationale Summary: Strong credit profiles."

@pytest.mark.asyncio
async def test_generate_explanation_timeout_fallback(coordinator_instance):
    """Verify local deterministic fallback is used during LLM timeouts."""
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", side_effect=mock_wait_for_with_cleanup(side_effect=asyncio.TimeoutError())):
        evidence = [
            {"category": "Financial Ratios", "source_agent": "FinancialHealthAgent", "severity": "HIGH", "title": "Low Current Ratio", "description": "0.8 ratio detected.", "recommendation": "Review working capital"}
        ]
        explanation = await coordinator_instance.generate_explanation(evidence)
        assert "Appraisal Summary (System Fallback):" in explanation
        assert "Negative/Warning Factors:" in explanation
        assert "Low Current Ratio: 0.8 ratio detected." in explanation

@pytest.mark.asyncio
async def test_generate_explanation_exception_fallback(coordinator_instance):
    """Verify local deterministic fallback is used during LLM general API exceptions."""
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", side_effect=mock_wait_for_with_cleanup(side_effect=RuntimeError("Groq Connection Error"))):
        evidence = [
            {"category": "Promoter Risk", "source_agent": "ManagementQualityAgent", "severity": "CRITICAL", "title": "Defaults Detected", "description": "Promoter default history", "recommendation": "CIBIL check required"}
        ]
        explanation = await coordinator_instance.generate_explanation(evidence)
        assert "Appraisal Summary (System Fallback):" in explanation
        assert "Defaults Detected: Promoter default history" in explanation

@pytest.mark.asyncio
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