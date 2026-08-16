# =============================================================================
# CREDENT — Loan Case State Machine (ASE-54)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
LoanCaseState — Tracks the step-by-step progress of a single appraisal case
through the coordinator pipeline. This allows:
  1. Dynamic agent skipping (e.g. no P&L → skip FinancialHealthAgent)
  2. Crash recovery (resume from last persisted step)
  3. Real-time case progress visibility
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Valid coordinator pipeline steps in order
PIPELINE_STEPS = [
    "init",
    "policy_loaded",
    "ingestion_complete",
    "agents_dispatched",
    "financial_complete",
    "management_complete",
    "sector_complete",
    "integrity_complete",
    "evidence_built",
    "cam_complete",
    "done",
]

# Valid case statuses
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_PAUSED = "PAUSED"


@dataclass
class LoanCaseState:
    """
    Represents the full state of a single credit appraisal case.
    Persisted to the loan_cases table after each major step completes.
    """
    case_id: str
    institution_id: str = "DEFAULT"
    status: str = STATUS_PENDING
    current_step: str = "init"

    # Dynamic routing flags — set during ingestion based on available data
    has_financials: bool = True   # False → skip FinancialHealthAgent
    has_promoters: bool = True    # False → skip ManagementAgent → auto MANUAL REVIEW

    # Accumulated outputs as the pipeline progresses
    extracted_data: dict = field(default_factory=dict)
    financial_result: dict = field(default_factory=dict)
    management_result: dict = field(default_factory=dict)
    sector_result: dict = field(default_factory=dict)
    integrity_result: dict = field(default_factory=dict)
    evidence_trail: list = field(default_factory=list)
    final_result: dict = field(default_factory=dict)

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def step_complete(self, step: str) -> None:
        """Advance to the next named step and mark state as RUNNING."""
        if step not in PIPELINE_STEPS:
            raise ValueError(f"Unknown pipeline step: '{step}'. Valid steps: {PIPELINE_STEPS}")
        self.current_step = step
        self.status = STATUS_RUNNING
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def step_failed(self, error: str) -> None:
        """Mark this case as FAILED with the error reason."""
        self.status = STATUS_FAILED
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_complete(self) -> None:
        """Mark this case as fully COMPLETED."""
        self.current_step = "done"
        self.status = STATUS_COMPLETED
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def detect_available_data(self, extracted_financials: dict) -> None:
        """
        Inspect extracted PDF data and set routing flags.
        Called immediately after ingestion completes.
        """
        # Check if meaningful financial data was extracted
        financial_indicators = [
            "current_assets", "current_liabilities", "total_debt",
            "net_operating_income", "annual_debt_service", "total_equity"
        ]
        found_financials = any(
            extracted_financials.get(k) not in (None, "", 0)
            for k in financial_indicators
        )
        self.has_financials = found_financials

        # Check if promoter/management data is present
        promoter_indicators = ["promoter_name", "director_name", "managing_director", "promoter_ids"]
        found_promoters = (
            any(extracted_financials.get(k) for k in promoter_indicators)
            or bool(extracted_financials.get("company_name"))
        )
        self.has_promoters = found_promoters

    @classmethod
    def from_db_record(cls, record: dict) -> "LoanCaseState":
        """Reconstruct a LoanCaseState from a database row (for crash recovery)."""
        state = cls(
            case_id=record["case_id"],
            institution_id=record.get("institution_id", "DEFAULT"),
            status=record.get("status", STATUS_PENDING),
            current_step=record.get("current_step", "init"),
            has_financials=record.get("has_financials", True),
            has_promoters=record.get("has_promoters", True),
        )
        # Restore any partial results that were already persisted
        result_data = record.get("result_data", {})
        state.extracted_data = result_data.get("extracted_data", {})
        state.financial_result = result_data.get("financial_result", {})
        state.management_result = result_data.get("management_result", {})
        state.sector_result = result_data.get("sector_result", {})
        state.integrity_result = result_data.get("integrity_result", {})
        return state

    def to_snapshot(self) -> dict:
        """Serialize partial results for DB persistence at each step."""
        return {
            "extracted_data": self.extracted_data,
            "financial_result": self.financial_result,
            "management_result": self.management_result,
            "sector_result": self.sector_result,
            "integrity_result": self.integrity_result,
            "evidence_trail": self.evidence_trail,
        }
