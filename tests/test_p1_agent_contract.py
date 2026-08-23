"""P1-5 - agent fallback cleanup.

The optional agents still return a placeholder payload when their LLM calls
fail, because changing their return types would be the massive rewrite the brief
rules out. What has changed is that the placeholder now carries a structured
failure marker, so a downstream reader can tell "nothing adverse was found" from
"the analysis never ran". Boundary validation is unchanged and still enforced.
"""

import asyncio

import pytest

from app.core.execution_state import (
    OPTIONAL_AGENTS,
    REQUIRED_AGENTS,
    AgentResult,
    AgentStatus,
    AnalysisStatus,
    AppraisalExecution,
    ErrorCode,
)
from app.core.output_validation import (
    validate,
    validate_research,
    validate_risk_intelligence,
    validate_sector_context,
)


# ---------------------------------------------------------------------------
# markers are present on the fallback payloads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_fallback_carries_structured_failure():
    from app.agents.input import realtime_intelligence as ri

    agent = ri.RealtimeIntelligenceAgent.__new__(ri.RealtimeIntelligenceAgent)
    agent.structured_llm = None
    agent.search = True

    class Boom:
        def __or__(self, other): return self
        def __ror__(self, other): return self
        async def ainvoke(self, *a, **k): raise RuntimeError("provider down")

    agent.llm = Boom()
    result = await agent.conduct_research("Anantara Agro Foods Pvt Ltd", "Agriculture")

    assert result["agent_status"] == "DEGRADED"
    assert result["error_code"] == "EXTERNAL_RESEARCH_UNAVAILABLE"
    assert result["research_degraded"] is True
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_research_input_guard_also_marked():
    """An empty company name is still a non-result and must say so."""
    from app.agents.input import realtime_intelligence as ri

    agent = ri.RealtimeIntelligenceAgent.__new__(ri.RealtimeIntelligenceAgent)
    agent.structured_llm = None
    agent.llm = None
    agent.search = True
    result = await agent.conduct_research("", "Agriculture")
    assert result["research_degraded"] is True
    assert result["agent_status"] == "DEGRADED"


def test_risk_fallback_marker_is_detected():
    payload = {
        "original_score": 65,
        "adjusted_score": 65,
        "adjustment_rationale": "AI analysis unavailable. Score returned unchanged. Manual review recommended.",
        "critical_flags": [],
        "agent_status": "DEGRADED",
        "error_code": "MODEL_UNAVAILABLE",
        "risk_analysis_degraded": True,
        "retryable": True,
    }
    status, code, reason = validate_risk_intelligence(payload)
    assert status is AgentStatus.DEGRADED
    assert code == "MODEL_UNAVAILABLE"
    assert "did not run" in reason


def test_sector_fallback_marker_is_detected():
    status, code, _ = validate_sector_context({
        "sector": "Unknown",
        "agent_status": "DEGRADED",
        "error_code": "MODEL_UNAVAILABLE",
        "sector_analysis_degraded": True,
    })
    assert status is AgentStatus.DEGRADED
    assert code == "MODEL_UNAVAILABLE"


def test_genuine_results_still_validate_as_success():
    """A real 'no adverse findings' result must NOT be treated as a failure."""
    ok_research, _, _ = validate_research({
        "company_news": ["No material adverse news identified."],
        "sector_headwinds": ["Stable demand outlook."],
        "litigation_signals": [],
    })
    assert ok_research is AgentStatus.SUCCESS

    ok_risk, _, _ = validate_risk_intelligence({"adjusted_score": 88, "critical_flags": []})
    assert ok_risk is AgentStatus.SUCCESS

    ok_sector, _, _ = validate_sector_context({"sector": "Agriculture", "outlook": "Stable"})
    assert ok_sector is AgentStatus.SUCCESS


def test_out_of_range_risk_score_degrades():
    status, code, _ = validate_risk_intelligence({"adjusted_score": 450})
    assert status is AgentStatus.DEGRADED
    assert code == ErrorCode.INVALID_OUTPUT.value


# ---------------------------------------------------------------------------
# markers propagate into the appraisal decision
# ---------------------------------------------------------------------------

def test_optional_agent_markers_degrade_but_do_not_block():
    execution = AppraisalExecution()
    for agent in REQUIRED_AGENTS:
        execution.record_success(agent)
    for agent, payload in [
        ("risk_intelligence", {"agent_status": "DEGRADED", "risk_analysis_degraded": True}),
        ("sector_context", {"agent_status": "DEGRADED", "sector_analysis_degraded": True}),
        ("realtime_intelligence", {"research_degraded": True}),
    ]:
        status, code, reason = validate(agent, payload)
        execution.record(AgentResult(agent=agent, status=status, error_code=code, reason=reason))

    assert execution.decision_allowed is True
    assert execution.status is AnalysisStatus.DEGRADED
    assert set(execution.degraded_components) == {
        "risk_intelligence", "sector_context", "realtime_intelligence",
    }


def test_no_fabricated_default_reaches_persistence_undetected():
    """Every optional agent failing must still be visible on the saved record."""
    from app.core.appraisal_safety import apply_safety_gate, persistence_fields

    result = {
        "individual_agent_outputs": {
            "ingestion": {"company_name": "Real Borrower Ltd", "total_revenue": 5000000,
                          "extraction_degraded": False},
            "financial_health": {"financial_health_score": 70, "ratios": {"dscr": 1.1},
                                 "metrics": {"revenue": 5000000}},
            "risk_intelligence": {"agent_status": "DEGRADED", "risk_analysis_degraded": True},
            "sector_context": {"agent_status": "DEGRADED", "sector_analysis_degraded": True},
            "web_research": {"research_degraded": True},
        },
        "combined_decision": {
            "document_control": {"status": "PENDING"},
            "five_cs": {k: {"evidence": "e", "assessment": "a", "risk_implication": "r"}
                        for k in ["character", "capacity", "capital", "collateral", "conditions"]},
            "decision": "APPROVE",
        },
    }
    summary, prov, ledger = apply_safety_gate(result)
    fields = persistence_fields(summary, prov, ledger)

    assert fields["analysis_status"] == AnalysisStatus.DEGRADED.value
    assert fields["decision_allowed"] is True
    for agent in ("risk_intelligence", "sector_context", "realtime_intelligence"):
        assert agent in fields["degraded_components"], f"{agent} degradation not recorded"


def test_required_and_optional_classification_unchanged():
    """P1-5 must not have quietly reclassified an agent."""
    assert "document_ingestion" in REQUIRED_AGENTS
    assert "financial_health" in REQUIRED_AGENTS
    assert "cam_generator" in REQUIRED_AGENTS
    assert "risk_intelligence" in OPTIONAL_AGENTS
    assert "sector_context" in OPTIONAL_AGENTS
    assert not (REQUIRED_AGENTS & OPTIONAL_AGENTS)
