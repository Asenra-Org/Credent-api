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
