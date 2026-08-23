"""P0-4 - explicit analysis state and fail-closed decision gating.

The dangerous behaviour this replaces:

    agent fails -> coordinator continues -> partial data -> CAM generated
    -> MANUAL REVIEW -> system reports success

MANUAL REVIEW is a legitimate human underwriting conclusion. A system failure
must never be dressed up as one, because a credit officer cannot tell the two
apart. This module draws that line explicitly:

    MANUAL_REVIEW_REQUIRED - the analysis completed; a human must decide.
    ANALYSIS_INCOMPLETE    - the analysis did not complete; there is no decision.

Agents report structured execution state. The coordinator knows which
components are REQUIRED for a valid credit decision, and refuses to allow one
when a required component did not produce a valid result.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class AnalysisStatus(str, Enum):
    """Lifecycle state of a whole appraisal."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"      # every required component succeeded
    DEGRADED = "DEGRADED"        # optional component(s) failed; decision still valid
    FAILED = "FAILED"            # a required component failed; no valid decision
    BLOCKED = "BLOCKED"          # refused on security grounds (e.g. prompt injection)


class AgentStatus(str, Enum):
    """Outcome of a single agent execution."""

    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"        # produced partial but usable output
    FAILED = "FAILED"            # produced nothing usable
    BLOCKED = "BLOCKED"          # refused to run (security)
    SKIPPED = "SKIPPED"          # not applicable to this case


# Sentinel decision values. ANALYSIS_INCOMPLETE must never be rendered as a
# credit conclusion by any client.
DECISION_ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"
DECISION_MANUAL_REVIEW = "MANUAL_REVIEW_REQUIRED"

# Components without which no credit decision may be issued. Ingestion and the
# CAM generator are structural: without them there are no figures and no memo.
# Financial health converts those figures into the ratios the decision rests on.
REQUIRED_AGENTS: frozenset[str] = frozenset({
    "document_ingestion",
    "financial_health",
    "cam_generator",
})

# Failure degrades the appraisal but does not invalidate the decision. These
# enrich an assessment rather than establish it.
OPTIONAL_AGENTS: frozenset[str] = frozenset({
    "risk_intelligence",
    "sector_context",
    "management_quality",
    "realtime_intelligence",
    "integrity_verification",
})


class ErrorCode(str, Enum):
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    NO_EXTRACTABLE_TEXT = "NO_EXTRACTABLE_TEXT"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"
    EXTERNAL_RESEARCH_UNAVAILABLE = "EXTERNAL_RESEARCH_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class AgentResult:
    """Structured execution state for one agent."""

    agent: str
    status: AgentStatus
    error_code: Optional[str] = None
    reason: Optional[str] = None
    retryable: bool = False
    data: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.status in (AgentStatus.SUCCESS, AgentStatus.DEGRADED)

    @property
    def required(self) -> bool:
        return self.agent in REQUIRED_AGENTS

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["status"] = self.status.value
        out.pop("data", None)   # never persist the payload here
        return out


@dataclass
class AppraisalExecution:
    """Aggregates agent results and decides whether a decision may be issued."""

    results: List[AgentResult] = field(default_factory=list)
    security_blocked: bool = False

    def record(self, result: AgentResult) -> AgentResult:
        self.results.append(result)
        return result

    def record_success(self, agent: str, data: Optional[Dict[str, Any]] = None) -> AgentResult:
        return self.record(AgentResult(agent=agent, status=AgentStatus.SUCCESS, data=data))

    def record_failure(
        self,
        agent: str,
        error_code: str = ErrorCode.UNKNOWN.value,
        reason: Optional[str] = None,
        retryable: bool = False,
    ) -> AgentResult:
        return self.record(AgentResult(
            agent=agent, status=AgentStatus.FAILED,
            error_code=error_code, reason=reason, retryable=retryable,
        ))

    def record_degraded(
        self,
        agent: str,
        error_code: str = ErrorCode.UNKNOWN.value,
        reason: Optional[str] = None,
    ) -> AgentResult:
        return self.record(AgentResult(
            agent=agent, status=AgentStatus.DEGRADED,
            error_code=error_code, reason=reason,
        ))

    # -- derived state ------------------------------------------------------
    @property
    def failed_required(self) -> List[str]:
        return sorted({r.agent for r in self.results if r.required and not r.ok})

    @property
    def failed_optional(self) -> List[str]:
        return sorted({r.agent for r in self.results if not r.required and not r.ok})

    @property
    def degraded_components(self) -> List[str]:
        """Every component that did not fully succeed, required or not."""
        return sorted({
            r.agent for r in self.results
            if r.status in (AgentStatus.FAILED, AgentStatus.DEGRADED, AgentStatus.BLOCKED)
        })

    @property
    def missing_required(self) -> List[str]:
        """Required agents that failed *or* never reported at all."""
        reported = {r.agent for r in self.results}
        never_ran = sorted(REQUIRED_AGENTS - reported)
        return sorted(set(self.failed_required) | set(never_ran))

    @property
    def decision_allowed(self) -> bool:
        """A credit decision may only be issued when every required agent succeeded."""
        return not self.security_blocked and not self.missing_required

    @property
    def status(self) -> AnalysisStatus:
        # A security refusal is reported as BLOCKED rather than FAILED: the
        # distinction matters to an operator, since one is a defence working
        # correctly and the other is the system breaking.
        if self.security_blocked or any(r.status is AgentStatus.BLOCKED for r in self.results):
            return AnalysisStatus.BLOCKED
        if self.missing_required:
            return AnalysisStatus.FAILED
        if self.degraded_components:
            return AnalysisStatus.DEGRADED
        return AnalysisStatus.COMPLETED

    def summary(self) -> Dict[str, Any]:
        return {
            "analysis_status": self.status.value,
            "decision_allowed": self.decision_allowed,
            "missing_required": self.missing_required,
            "failed_optional": self.failed_optional,
            "degraded_components": self.degraded_components,
            "agent_results": [r.to_dict() for r in self.results],
        }


def classify_exception(exc: BaseException) -> tuple[str, bool]:
    """Map an exception to (error_code, retryable) without leaking payloads."""
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    if "rate limit" in text or "rate_limit" in text or "429" in text:
        return ErrorCode.MODEL_RATE_LIMITED.value, True
    if "timeout" in text or "timeout" in name:
        return ErrorCode.MODEL_TIMEOUT.value, True
    if "413" in text or "too large" in text:
        return ErrorCode.MODEL_UNAVAILABLE.value, False
    if "validation" in name or "json" in text or "parse" in text:
        return ErrorCode.INVALID_OUTPUT.value, False
    if "connection" in text or "unavailable" in text or "503" in text:
        return ErrorCode.MODEL_UNAVAILABLE.value, True
    return ErrorCode.UNKNOWN.value, False


def gate_decision(execution: AppraisalExecution, proposed_decision: Any) -> Dict[str, Any]:
    """Apply the fail-closed rule to a proposed decision.

    When a required component is missing the decision is replaced with
    ANALYSIS_INCOMPLETE - explicitly *not* MANUAL REVIEW, which would read as a
    genuine underwriting conclusion.
    """
    if execution.decision_allowed:
        return {
            "decision": proposed_decision,
            "decision_allowed": True,
            "analysis_status": execution.status.value,
            "degraded_components": execution.degraded_components,
        }
    return {
        "decision": DECISION_ANALYSIS_INCOMPLETE,
        "decision_allowed": False,
        "analysis_status": execution.status.value,
        "missing_required": execution.missing_required,
        "degraded_components": execution.degraded_components,
        "recommended_loan_amount": "UNAVAILABLE",
        "recommended_interest_rate": "UNAVAILABLE",
        "decision_rationale": (
            "Credit recommendation unavailable: required analysis did not "
            "complete (" + ", ".join(execution.missing_required) + "). "
            "This is a system failure, not an underwriting conclusion."
        ),
    }
