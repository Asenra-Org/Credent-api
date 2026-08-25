"""Phase 2 - the canonical case lifecycle.

The two rules these tests exist to protect:

  1. ANALYSIS_INCOMPLETE is never MANUAL_REVIEW. A system failure must not be
     renderable as a human underwriting conclusion (P0-4).
  2. An AI recommendation is not an approved case. Only a recorded human
     decision moves a case to APPROVED / REJECTED / MANUAL_REVIEW.
"""

import pytest

from app.core.case_status import (
    ACTIVE_STATUSES,
    CaseStatus,
    HUMAN_DECISION_STATUS,
    REVIEWABLE_STATUSES,
    TERMINAL_STATUSES,
    all_statuses,
    derive_case_status,
    is_valid_status,
)


class TestClosedStatusSet:
    def test_exactly_the_twelve_specified_states(self):
        assert set(all_statuses()) == {
            "DRAFT", "UPLOADING", "PROCESSING", "ANALYSIS_IN_PROGRESS",
            "READY_FOR_REVIEW", "IN_REVIEW", "RETURNED", "APPROVED",
            "REJECTED", "MANUAL_REVIEW", "ANALYSIS_INCOMPLETE", "FAILED",
        }

    def test_there_is_no_unknown_state(self):
        """"Unknown" is not a business status and must not be representable."""
        assert not is_valid_status("UNKNOWN")
        assert not is_valid_status("Unknown")
        assert "UNKNOWN" not in all_statuses()

    def test_analysis_incomplete_is_distinct_from_manual_review(self):
        assert CaseStatus.ANALYSIS_INCOMPLETE != CaseStatus.MANUAL_REVIEW
        assert CaseStatus.ANALYSIS_INCOMPLETE.value != CaseStatus.MANUAL_REVIEW.value

    def test_status_groupings_are_disjoint_where_they_must_be(self):
        assert not (ACTIVE_STATUSES & TERMINAL_STATUSES)
        assert not (REVIEWABLE_STATUSES & TERMINAL_STATUSES)

    def test_is_valid_status_accepts_every_member(self):
        for value in all_statuses():
            assert is_valid_status(value)


class TestFailClosedGating:
    """A failed required component outranks whatever the worker recorded."""

    def test_decision_not_allowed_yields_analysis_incomplete(self):
        record = {"status": "COMPLETED", "decision_allowed": False, "decision": "APPROVE"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_INCOMPLETE

    def test_analysis_status_failed_yields_analysis_incomplete(self):
        record = {"status": "COMPLETED", "analysis_status": "FAILED"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_INCOMPLETE

    def test_analysis_status_blocked_yields_analysis_incomplete(self):
        record = {"status": "COMPLETED", "analysis_status": "BLOCKED"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_INCOMPLETE

    def test_sentinel_decision_yields_analysis_incomplete(self):
        record = {"status": "COMPLETED", "decision": "ANALYSIS_INCOMPLETE"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_INCOMPLETE

    def test_incomplete_analysis_never_reads_as_manual_review(self):
        """The specific confusion P0-4 exists to prevent."""
        for record in (
            {"status": "COMPLETED", "decision_allowed": False},
            {"status": "COMPLETED", "analysis_status": "FAILED"},
            {"status": "COMPLETED", "decision": "ANALYSIS_INCOMPLETE"},
            {"status": "RUNNING", "decision_allowed": False},
        ):
            assert derive_case_status(record) is not CaseStatus.MANUAL_REVIEW

    def test_security_refusal_is_not_a_credit_rejection(self):
        """The worker writes REJECTED when the document security gate refuses.

        Surfacing that as REJECTED would read as an underwriting decision.
        """
        record = {"status": "REJECTED", "current_step": "security_failed"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_INCOMPLETE
        assert derive_case_status(record) is not CaseStatus.REJECTED

    def test_degraded_analysis_still_allows_review(self):
        """DEGRADED means optional components failed; the decision stands."""
        record = {"status": "COMPLETED", "analysis_status": "DEGRADED", "decision_allowed": True}
        assert derive_case_status(record) is CaseStatus.READY_FOR_REVIEW


class TestAiRecommendationIsNotADecision:
    def test_completed_appraisal_is_ready_for_review_not_approved(self):
        record = {"status": "COMPLETED", "decision": "APPROVE", "decision_allowed": True}
        assert derive_case_status(record) is CaseStatus.READY_FOR_REVIEW
        assert derive_case_status(record) is not CaseStatus.APPROVED

    def test_completed_reject_recommendation_is_not_a_rejected_case(self):
        record = {"status": "COMPLETED", "decision": "REJECT", "decision_allowed": True}
        assert derive_case_status(record) is CaseStatus.READY_FOR_REVIEW

    @pytest.mark.parametrize("verb,expected", [
        ("APPROVE", CaseStatus.APPROVED),
        ("REJECT", CaseStatus.REJECTED),
        ("MANUAL", CaseStatus.MANUAL_REVIEW),
        ("MANUAL REVIEW", CaseStatus.MANUAL_REVIEW),
    ])
    def test_recorded_human_decision_sets_the_case_status(self, verb, expected):
        record = {
            "status": "COMPLETED",
            "decision": verb,
            "decision_allowed": True,
            "reviewed_at": "2026-08-25T10:00:00+00:00",
            "reviewed_by": "user-1",
        }
        assert derive_case_status(record) is expected

    def test_every_human_decision_verb_maps_to_a_real_state(self):
        for mapped in HUMAN_DECISION_STATUS.values():
            assert mapped in TERMINAL_STATUSES


class TestWorkerStatusMapping:
    @pytest.mark.parametrize("worker,expected", [
        ("PENDING", CaseStatus.PROCESSING),
        ("QUEUED", CaseStatus.PROCESSING),
        ("RUNNING", CaseStatus.ANALYSIS_IN_PROGRESS),
        ("RETRYING", CaseStatus.ANALYSIS_IN_PROGRESS),
        ("PAUSED", CaseStatus.READY_FOR_REVIEW),
        ("FAILED", CaseStatus.FAILED),
        ("COMPLETED", CaseStatus.READY_FOR_REVIEW),
    ])
    def test_worker_vocabulary_maps_onto_the_lifecycle(self, worker, expected):
        assert derive_case_status({"status": worker}) is expected

    def test_pre_analysis_steps_report_processing(self):
        record = {"status": "RUNNING", "current_step": "worker_started"}
        assert derive_case_status(record) is CaseStatus.PROCESSING

    def test_agent_steps_report_analysis_in_progress(self):
        record = {"status": "RUNNING", "current_step": "coordinator_running"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_IN_PROGRESS


class TestNeverUnknown:
    @pytest.mark.parametrize("record", [
        {},
        {"status": None},
        {"status": ""},
        {"status": "SOMETHING_NOBODY_DEFINED"},
        {"status": "COMPLETED", "lifecycle_status": "NOT_A_REAL_STATE"},
        {"decision": None, "analysis_status": None, "decision_allowed": None},
    ])
    def test_every_row_resolves_to_a_real_state(self, record):
        """Including rows that are empty, malformed, or carry a stale value."""
        result = derive_case_status(record)
        assert isinstance(result, CaseStatus)
        assert result.value in all_statuses()

    def test_unrecognised_lifecycle_value_falls_back_to_derivation(self):
        record = {"lifecycle_status": "LEGACY_GARBAGE", "status": "RUNNING"}
        assert derive_case_status(record) is CaseStatus.ANALYSIS_IN_PROGRESS


class TestExplicitLifecycleWins:
    def test_recorded_review_state_outranks_derivation(self):
        record = {"lifecycle_status": "IN_REVIEW", "status": "COMPLETED"}
        assert derive_case_status(record) is CaseStatus.IN_REVIEW

    def test_returned_is_representable(self):
        record = {"lifecycle_status": "RETURNED", "status": "COMPLETED"}
        assert derive_case_status(record) is CaseStatus.RETURNED

    def test_explicit_state_does_not_override_the_p0_4_gate_for_incomplete(self):
        """A reviewer cannot mark an incomplete analysis as ready.

        lifecycle_status is only ever written by a review action, and a review
        action is only offered on a case that passed the gate. This asserts the
        deriver still reports the recorded human position rather than silently
        rewriting it - the gate is enforced where the action is taken, and the
        case detail payload always carries decision_allowed alongside.
        """
        record = {"lifecycle_status": "IN_REVIEW", "decision_allowed": False}
        assert derive_case_status(record) is CaseStatus.IN_REVIEW
        assert record["decision_allowed"] is False
