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
    DECISION_ANALYSIS_INCOMPLETE,
    AgentResult,
    AgentStatus,
    AnalysisStatus,
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


# ---------------------------------------------------------------------------
# [P0-4] Gate for the standalone CAM endpoint.
#
# apply_safety_gate() judges a whole coordinator run: it requires every agent in
# REQUIRED_AGENTS to have reported, financial_health included. POST
# /reports/generate-cam never receives a financial_health payload - it is handed
# only the extracted document data and produces a CAM - so running the full gate
# there would mark every successful CAM incomplete.
#
# This applies the same validators, scoped to the two agents that endpoint
# genuinely has evidence for. It exists because a total provider failure (every
# agent returning placeholders after HTTP 402) was reaching the UI as
# "MANUAL REVIEW" - a human underwriting conclusion - rather than as the system
# failure it actually was.
# ---------------------------------------------------------------------------

CAM_ENDPOINT_AGENTS = ("document_ingestion", "cam_generator")


def gate_cam_response(extracted_pdf_data, cam_result):
    """Gate a standalone CAM response. Returns (result, summary).

    The result is returned unchanged when both the extraction and the CAM are
    usable. When either failed, the credit recommendation is replaced with
    ANALYSIS_INCOMPLETE - explicitly not MANUAL REVIEW, which a credit officer
    would read as a human conclusion.

    The caller's dict is not mutated; a copy is returned.
    """
    execution = AppraisalExecution()
    for agent, payload in (
        ("document_ingestion", extracted_pdf_data),
        ("cam_generator", cam_result),
    ):
        status, error_code, reason = validate_agent_output(agent, payload)
        execution.record(AgentResult(
            agent=agent, status=status, error_code=error_code, reason=reason,
        ))

    failed = [r.agent for r in execution.results if not r.ok]
    degraded = [
        r.agent for r in execution.results
        if r.status in (AgentStatus.DEGRADED, AgentStatus.FAILED, AgentStatus.BLOCKED)
    ]
    blocked = any(r.status is AgentStatus.BLOCKED for r in execution.results)

    if blocked:
        analysis_status = AnalysisStatus.BLOCKED.value
    elif failed:
        analysis_status = AnalysisStatus.FAILED.value
    elif degraded:
        analysis_status = AnalysisStatus.DEGRADED.value
    else:
        analysis_status = AnalysisStatus.COMPLETED.value

    decision_allowed = not failed and not blocked

    summary = {
        "analysis_status": analysis_status,
        "decision_allowed": decision_allowed,
        "missing_required": sorted(failed),
        "degraded_components": sorted(degraded),
        "agent_results": [r.to_dict() for r in execution.results],
    }

    result = dict(cam_result or {})
    # These travel with the payload either way, so the client can always tell a
    # gated response from an ungated one.
    result["analysis_status"] = analysis_status
    result["decision_allowed"] = decision_allowed
    result["missing_required"] = summary["missing_required"]
    result["degraded_components"] = summary["degraded_components"]

    if not decision_allowed:
        result["decision"] = DECISION_ANALYSIS_INCOMPLETE
        result["recommended_loan_amount"] = "UNAVAILABLE"
        result["recommended_interest_rate"] = "UNAVAILABLE"
        result["decision_rationale"] = (
            "Credit recommendation unavailable: required analysis did not "
            "complete (" + ", ".join(sorted(failed)) + "). This is a system "
            "failure, not an underwriting conclusion."
        )
        # A recommendation block from a failed run must not survive either.
        if isinstance(result.get("recommendation"), dict):
            result["recommendation"] = dict(result["recommendation"])
            result["recommendation"]["decision"] = DECISION_ANALYSIS_INCOMPLETE

    return result, summary
