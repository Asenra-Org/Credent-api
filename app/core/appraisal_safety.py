"""Shared safety path for appraisal completion.

Both entry points - the synchronous API route (``app/routes/documents.py``) and
the Celery/background worker (``app/services/appraisal_worker.py``) - must apply
identical validation, execution-state and decision-gating rules. Duplicating that
logic would guarantee the two drift apart, and the worker path silently keeping
the old fail-open behaviour is exactly the class of bug P0-4 exists to prevent.

This module owns the rules once. Callers hand it the coordinator's outputs and
receive back the execution summary, the gated decision and the provenance ledger.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.core.execution_state import (
    AgentResult,
    AppraisalExecution,
    gate_decision,
)
from app.core.output_validation import validate as validate_agent_output
from app.core.provenance import ProvenanceLedger

# Maps the coordinator's ``individual_agent_outputs`` keys onto the agent names
# used by REQUIRED_AGENTS / the validators. Keeping this in one place means both
# entry points classify the same payload the same way.
OUTPUT_KEY_TO_AGENT: Dict[str, str] = {
    "ingestion": "document_ingestion",
    "financial_health": "financial_health",
    "risk_intelligence": "risk_intelligence",
    "sector_context": "sector_context",
    "management_quality": "management_quality",
    "web_research": "realtime_intelligence",
    "integrity_check": "integrity_verification",
}


def collect_agent_payloads(appraisal_result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the per-agent payloads a coordinator run produced."""
    outputs = (appraisal_result or {}).get("individual_agent_outputs", {}) or {}
    payloads: Dict[str, Any] = {
        agent: outputs.get(key) for key, agent in OUTPUT_KEY_TO_AGENT.items()
    }
    # The CAM is surfaced at the top level rather than inside the agent outputs.
    payloads["cam_generator"] = appraisal_result.get("combined_decision")
    return payloads


def build_execution(
    appraisal_result: Dict[str, Any],
    security_blocked: bool = False,
) -> AppraisalExecution:
    """Validate every agent output and assemble the execution state."""
    execution = AppraisalExecution()
    for agent, payload in collect_agent_payloads(appraisal_result).items():
        status, error_code, reason = validate_agent_output(agent, payload)
        execution.record(AgentResult(
            agent=agent, status=status, error_code=error_code, reason=reason,
        ))
    if security_blocked:
        execution.security_blocked = True
    return execution


def capture_provenance(
    ingestion_agent: Any = None,
    cam_agent: Any = None,
    coordinator: Any = None,
) -> ProvenanceLedger:
    """Record provenance for the agents that actually ran.

    Model handles are resolved from the live agent objects, so a fallback
    rollover in ``app.core.llm`` is recorded truthfully rather than assumed from
    the environment.
    """
    ledger = ProvenanceLedger()

    def _llm_of(*candidates: Any) -> Optional[Any]:
        for candidate in candidates:
            llm = getattr(candidate, "llm", None)
            if llm is not None:
                return llm
        return None

    ingestion_llm = _llm_of(ingestion_agent, getattr(coordinator, "ingestion_agent", None))
    cam_llm = _llm_of(cam_agent, getattr(coordinator, "cam_agent", None))

    ledger.record_capture("document_ingestion", llm=ingestion_llm)
    ledger.record_capture("cam_generator", llm=cam_llm)

    for attr, agent_name in (
        ("financial_agent", "financial_health"),
        ("sector_agent", "sector_context"),
        ("management_agent", "management_quality"),
    ):
        agent_obj = getattr(coordinator, attr, None)
        if agent_obj is not None:
            ledger.record_capture(agent_name, llm=getattr(agent_obj, "llm", None))

    return ledger


def apply_safety_gate(
    appraisal_result: Dict[str, Any],
    ingestion_agent: Any = None,
    coordinator: Any = None,
    security_blocked: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], ProvenanceLedger]:
    """Apply P0-2 and P0-4 to a completed appraisal, in place.

    Returns ``(execution_summary, provenance_summary, ledger)``. When a REQUIRED
    agent failed, the surfaced recommendation is overwritten with
    ANALYSIS_INCOMPLETE so no client can render an incomplete analysis as a
    credit conclusion.
    """
    execution = build_execution(appraisal_result, security_blocked=security_blocked)
    summary = execution.summary()

    proposed = (appraisal_result.get("combined_decision") or {}).get("decision")
    gated = gate_decision(execution, proposed)

    appraisal_result["analysis_status"] = summary["analysis_status"]
    appraisal_result["decision_allowed"] = summary["decision_allowed"]
    appraisal_result["degraded_components"] = summary["degraded_components"]
    appraisal_result["missing_required"] = summary["missing_required"]
    appraisal_result["agent_results"] = summary["agent_results"]

    if not summary["decision_allowed"]:
        combined = appraisal_result.get("combined_decision") or {}
        combined.update({
            "decision": gated["decision"],
            "recommended_loan_amount": gated["recommended_loan_amount"],
            "recommended_interest_rate": gated["recommended_interest_rate"],
            "decision_rationale": gated["decision_rationale"],
        })
        appraisal_result["combined_decision"] = combined

    ledger = capture_provenance(
        ingestion_agent=ingestion_agent,
        cam_agent=getattr(coordinator, "cam_agent", None),
        coordinator=coordinator,
    )
    provenance_summary = ledger.summary()
    appraisal_result["provenance"] = {
        "summary": provenance_summary,
        "agents": ledger.to_list(),
    }
    return summary, provenance_summary, ledger


def persistence_fields(
    execution_summary: Dict[str, Any],
    provenance_summary: Dict[str, Any],
    ledger: ProvenanceLedger,
) -> Dict[str, Any]:
    """The provenance/state fields both entry points pass to ``save_appraisal``.

    Incomplete appraisals are still persisted - the audit trail matters - but
    they carry ``decision_allowed=False`` and an explicit analysis status so a
    failed run can never be mistaken for a credit decision.
    """
    return {
        "provenance_summary": provenance_summary,
        "agent_provenance": ledger.to_list(),
        "analysis_status": execution_summary["analysis_status"],
        "degraded_components": execution_summary["degraded_components"],
        "decision_allowed": execution_summary["decision_allowed"],
    }
