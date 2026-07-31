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
