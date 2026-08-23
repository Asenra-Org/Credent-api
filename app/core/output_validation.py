"""P0-4B - validate agent output before it is allowed to influence a decision.

An LLM response that parses as JSON is not automatically a valid credit input.
These validators answer one question per agent: *did this agent actually produce
a usable result, or is it a placeholder wearing the shape of one?*

Invalid output yields FAILED or DEGRADED - never SUCCESS with defaults.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.execution_state import AgentStatus, ErrorCode

# Values the pipeline historically substituted for real data. Their presence in
# an identity field is proof the agent did not extract anything.
PLACEHOLDER_MARKERS: frozenset[str] = frozenset({
    "unknown entity", "unknown", "n/a", "na", "not provided",
    "not computable", "missing", "unable to extract", "",
})

# The fabricated score the pipeline emitted when extraction failed. Treated as a
# signal only when it arrives with no supporting financial data.
FABRICATED_FALLBACK_SCORE = 65


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_MARKERS


def validate_ingestion(payload: Optional[Dict[str, Any]]) -> Tuple[AgentStatus, Optional[str], Optional[str]]:
    """Validate document ingestion output.

    Returns ``(status, error_code, reason)``. Ingestion is REQUIRED, so a
    placeholder identity plus no financial figures must fail rather than pass a
    fabricated borrower downstream.
    """
    if not payload or not isinstance(payload, dict):
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "no ingestion payload"

    # The agent's own self-report is authoritative when present.
    if payload.get("extraction_degraded") is True:
        return (
            AgentStatus.FAILED,
            ErrorCode.INVALID_OUTPUT.value,
            "extraction reported degraded",
        )

    security = payload.get("security") or {}
    if security.get("status") == "REJECTED" or security.get("prompt_injection"):
        return AgentStatus.BLOCKED, ErrorCode.SECURITY_BLOCKED.value, "document blocked by security gate"

    name_missing = _is_placeholder(payload.get("company_name"))
    financial_fields = (
        "total_revenue", "total_debt", "shareholder_equity",
        "current_assets", "current_liabilities",
    )
    figures = [payload.get(f) for f in financial_fields]
    has_figures = any(isinstance(v, (int, float)) and v not in (0, None) for v in figures)

    if name_missing and not has_figures:
        return (
            AgentStatus.FAILED,
            ErrorCode.INVALID_OUTPUT.value,
            "no borrower identity and no financial figures extracted",
        )

    # The specific historical false-success signature.
    if (
        name_missing
        and payload.get("base_score") == FABRICATED_FALLBACK_SCORE
        and not has_figures
    ):
        return (
            AgentStatus.FAILED,
            ErrorCode.INVALID_OUTPUT.value,
            "placeholder extraction with fabricated fallback score",
        )

    if name_missing or not has_figures:
        return (
            AgentStatus.DEGRADED,
            ErrorCode.INVALID_OUTPUT.value,
            "partial extraction: missing borrower identity or financial figures",
        )

    score = payload.get("base_score")
    if score is not None and not (isinstance(score, (int, float)) and 0 <= score <= 100):
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "base_score out of range"

    return AgentStatus.SUCCESS, None, None


def validate_cam(payload: Optional[Dict[str, Any]]) -> Tuple[AgentStatus, Optional[str], Optional[str]]:
    """Validate CAM generator output.

    The error fallback in ``generate_cam`` sets ``document_control.status`` to
    ERROR and flattens ``five_cs`` to bare strings; both are detected here.
    """
    if not payload or not isinstance(payload, dict):
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "no CAM payload"

    control = payload.get("document_control") or {}
    if str(control.get("status", "")).upper() == "ERROR":
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "CAM generation reported ERROR"

    five_cs = payload.get("five_cs")
    if not isinstance(five_cs, dict) or not five_cs:
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "CAM missing five_cs"

    # The error fallback emits {"character": "N/A", ...} - flat strings rather
    # than the {evidence, assessment, risk_implication} objects.
    flat = [k for k, v in five_cs.items() if not isinstance(v, dict)]
    if len(flat) == len(five_cs):
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "five_cs collapsed to placeholder strings"

    populated = 0
    for value in five_cs.values():
        if isinstance(value, dict) and not _is_placeholder(value.get("assessment")):
            populated += 1
    if populated == 0:
        return AgentStatus.DEGRADED, ErrorCode.INVALID_OUTPUT.value, "no 5C assessment populated"

    return AgentStatus.SUCCESS, None, None


def validate_financial_health(payload: Optional[Dict[str, Any]]) -> Tuple[AgentStatus, Optional[str], Optional[str]]:
    """Validate financial health output. REQUIRED: ratios drive the decision."""
    if not payload or not isinstance(payload, dict):
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "no financial health payload"

    score = payload.get("financial_health_score")
    ratios = payload.get("ratios") or {}
    metrics = payload.get("metrics") or {}

    if score is None and not ratios and not metrics:
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "no score, ratios or metrics produced"
    if score is not None and not (isinstance(score, (int, float)) and 0 <= score <= 100):
        return AgentStatus.FAILED, ErrorCode.INVALID_OUTPUT.value, "financial_health_score out of range"
    if not ratios:
        return AgentStatus.DEGRADED, ErrorCode.INVALID_OUTPUT.value, "no ratios computed"
    return AgentStatus.SUCCESS, None, None


def validate_research(payload: Optional[Dict[str, Any]]) -> Tuple[AgentStatus, Optional[str], Optional[str]]:
    """Validate external research. OPTIONAL: unavailability degrades, never fails."""
    if not payload or not isinstance(payload, dict):
        return (
            AgentStatus.DEGRADED,
            ErrorCode.EXTERNAL_RESEARCH_UNAVAILABLE.value,
            "external research unavailable",
        )
    if payload.get("research_degraded") is True:
        return (
            AgentStatus.DEGRADED,
            ErrorCode.EXTERNAL_RESEARCH_UNAVAILABLE.value,
            "external research unavailable",
        )
    return AgentStatus.SUCCESS, None, None


VALIDATORS = {
    "document_ingestion": validate_ingestion,
    "cam_generator": validate_cam,
    "financial_health": validate_financial_health,
    "realtime_intelligence": validate_research,
}


def validate(agent: str, payload: Optional[Dict[str, Any]]):
    """Dispatch to the validator for ``agent``.

    Agents without a bespoke validator still get a baseline check: an agent that
    produced nothing at all did not succeed, whatever its role. Required agents
    fail; optional ones degrade so the appraisal continues but stays honest.
    """
    validator = VALIDATORS.get(agent)
    if validator is not None:
        return validator(payload)

    if not payload:
        # Imported here to avoid a circular import at module load.
        from app.core.execution_state import REQUIRED_AGENTS

        status = AgentStatus.FAILED if agent in REQUIRED_AGENTS else AgentStatus.DEGRADED
        return status, ErrorCode.INVALID_OUTPUT.value, "agent produced no output"

    return AgentStatus.SUCCESS, None, None
