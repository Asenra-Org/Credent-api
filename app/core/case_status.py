"""Canonical case lifecycle for CRESEM.

The persistence layer grew three overlapping vocabularies:

  * ``loan_cases.status``    - PENDING / RUNNING / RETRYING / PAUSED / COMPLETED
                               / FAILED / REJECTED, written by the worker
  * ``analysis_status``      - the P0-4 execution state (COMPLETED / DEGRADED /
                               FAILED / BLOCKED), which says whether the analysis
                               produced a usable result
  * ``decision``             - the credit recommendation, which is a *proposal*
                               until a human underwriter records a decision

None of those is the thing a credit officer needs to see in a queue. This module
owns the single business lifecycle the product renders, and the rules for
deriving it from the three sources above.

Two invariants matter more than the rest:

1. **ANALYSIS_INCOMPLETE is never MANUAL_REVIEW.** MANUAL_REVIEW is a human
   underwriting conclusion; ANALYSIS_INCOMPLETE means the system did not finish
   and there is no conclusion at all. Collapsing them would let a failure be
   read as a decision, which is exactly what P0-4 exists to prevent.

2. **An AI recommendation never becomes an approved case.** A completed
   appraisal with ``decision == "APPROVE"`` is READY_FOR_REVIEW, not APPROVED.
   Only a recorded human decision moves a case to APPROVED / REJECTED /
   MANUAL_REVIEW.

There is deliberately no UNKNOWN member. Every row resolves to a real state.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class CaseStatus(str, Enum):
    """The lifecycle states the product renders. This list is closed."""

    DRAFT = "DRAFT"                              # created, nothing submitted yet
    UPLOADING = "UPLOADING"                      # documents being received
    PROCESSING = "PROCESSING"                    # queued / extraction underway
    ANALYSIS_IN_PROGRESS = "ANALYSIS_IN_PROGRESS"  # agents running
    READY_FOR_REVIEW = "READY_FOR_REVIEW"        # analysis done, awaiting a human
    IN_REVIEW = "IN_REVIEW"                      # a reviewer has picked it up
    RETURNED = "RETURNED"                        # sent back to the analyst
    APPROVED = "APPROVED"                        # human decision
    REJECTED = "REJECTED"                        # human decision
    MANUAL_REVIEW = "MANUAL_REVIEW"              # human decision: needs deeper review
    ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"  # required analysis did not complete
    FAILED = "FAILED"                            # infrastructure failure


#: States in which no further automated work is expected.
TERMINAL_STATUSES = frozenset({
    CaseStatus.APPROVED,
    CaseStatus.REJECTED,
    CaseStatus.MANUAL_REVIEW,
    CaseStatus.FAILED,
    CaseStatus.ANALYSIS_INCOMPLETE,
})

#: States that belong in an underwriter's queue.
REVIEWABLE_STATUSES = frozenset({
    CaseStatus.READY_FOR_REVIEW,
    CaseStatus.IN_REVIEW,
})

#: States where the pipeline is still working.
ACTIVE_STATUSES = frozenset({
    CaseStatus.UPLOADING,
    CaseStatus.PROCESSING,
    CaseStatus.ANALYSIS_IN_PROGRESS,
})

#: Human decisions, mapped from the recorded ``decision`` verb.
HUMAN_DECISION_STATUS: Dict[str, CaseStatus] = {
    "APPROVE": CaseStatus.APPROVED,
    "APPROVED": CaseStatus.APPROVED,
    "REJECT": CaseStatus.REJECTED,
    "REJECTED": CaseStatus.REJECTED,
    "MANUAL": CaseStatus.MANUAL_REVIEW,
    "MANUAL_REVIEW": CaseStatus.MANUAL_REVIEW,
    "MANUAL REVIEW": CaseStatus.MANUAL_REVIEW,
    "PENDING": CaseStatus.MANUAL_REVIEW,
}

# Worker vocabulary -> lifecycle, for the cases where the mapping is unconditional.
_WORKER_STATUS_DIRECT: Dict[str, CaseStatus] = {
    "PENDING": CaseStatus.PROCESSING,
    "QUEUED": CaseStatus.PROCESSING,
    "RUNNING": CaseStatus.ANALYSIS_IN_PROGRESS,
    "RETRYING": CaseStatus.ANALYSIS_IN_PROGRESS,
    "PAUSED": CaseStatus.READY_FOR_REVIEW,
    "FAILED": CaseStatus.FAILED,
}

# Steps that run before any agent does, so the case is still "processing"
# rather than "analysis in progress".
# Compared against _norm() output, which upper-cases.
_PRE_ANALYSIS_STEPS = frozenset({
    "INIT", "WORKER_STARTED", "DOWNLOAD", "INGESTION", "SECURITY_SCAN",
})


def _norm(value: Any) -> Optional[str]:
    """Upper-case a value for comparison, or None when it is not usable text."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _analysis_blocked(analysis_status: Any, decision_allowed: Any, decision: Any) -> bool:
    """True when P0-4 says no credit decision may be issued for this case.

    Read from whichever of the three signals is present. ``decision_allowed`` is
    authoritative when it was recorded; older rows predate the column and are
    judged on ``analysis_status`` and the sentinel decision value instead.
    """
    if decision_allowed is not None and not bool(decision_allowed):
        return True
    if _norm(analysis_status) in {"FAILED", "BLOCKED"}:
        return True
    if _norm(decision) == "ANALYSIS_INCOMPLETE":
        return True
    return False


def derive_case_status(record: Dict[str, Any]) -> CaseStatus:
    """Resolve one persisted case row to its lifecycle state.

    ``record`` is a ``loan_cases`` row (as returned by ``get_case``), optionally
    enriched with the appraisal's ``analysis_status`` / ``decision_allowed`` /
    ``decision`` fields.

    Precedence, highest first:

    1. ``lifecycle_status`` - an explicitly recorded review state (IN_REVIEW,
       RETURNED, or a human decision). A human's recorded position on the case
       outranks anything derived.
    2. A recorded human decision (``reviewed_at`` plus ``decision``).
    3. The P0-4 gate - a case whose required analysis failed is
       ANALYSIS_INCOMPLETE regardless of what the worker status says.
    4. The worker's execution status.
    """
    record = record or {}

    # 1. Explicit lifecycle state wins.
    explicit = _norm(record.get("lifecycle_status"))
    if explicit:
        try:
            return CaseStatus(explicit)
        except ValueError:
            # An unrecognised stored value must not become "Unknown"; fall
            # through and derive the state from the execution signals instead.
            pass

    analysis_status = record.get("analysis_status")
    decision_allowed = record.get("decision_allowed")
    decision = record.get("decision")
    worker_status = _norm(record.get("status"))
    current_step = _norm(record.get("current_step"))

    # 2. A recorded human decision outranks the pipeline's own opinion.
    if record.get("reviewed_at"):
        mapped = HUMAN_DECISION_STATUS.get(_norm(decision) or "")
        if mapped is not None:
            return mapped
        return CaseStatus.MANUAL_REVIEW

    # 3. Fail-closed gate. Checked before the worker status so a run that
    #    "completed" without a required agent is never shown as reviewable.
    if _analysis_blocked(analysis_status, decision_allowed, decision):
        return CaseStatus.ANALYSIS_INCOMPLETE

    # 4. Worker execution status.
    if worker_status == "COMPLETED":
        return CaseStatus.READY_FOR_REVIEW

    if worker_status == "REJECTED":
        # The worker writes REJECTED when the document security gate refuses the
        # file. That is a refusal to analyse, not a credit rejection - surfacing
        # it as REJECTED would read as an underwriting decision.
        return CaseStatus.ANALYSIS_INCOMPLETE

    if worker_status == "RUNNING" and current_step in _PRE_ANALYSIS_STEPS:
        return CaseStatus.PROCESSING

    if worker_status in _WORKER_STATUS_DIRECT:
        return _WORKER_STATUS_DIRECT[worker_status]

    if worker_status == "DRAFT":
        return CaseStatus.DRAFT

    # A row with no usable status is a case that was created but never started.
    return CaseStatus.DRAFT


def is_valid_status(value: Any) -> bool:
    """True when ``value`` names a lifecycle state."""
    try:
        CaseStatus(_norm(value) or "")
        return True
    except ValueError:
        return False


def all_statuses() -> list[str]:
    return [s.value for s in CaseStatus]
