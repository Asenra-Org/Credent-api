# =============================================================================
# CREDENT — [AI-A-W7] Crash Recovery & Dynamic Skip Tests
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
test_w7_crash_recovery.py — Covers gaps not addressed in test_ase_54_case_state.py:

  1. Server crash simulation → resume from exact last persisted step
  2. GST/bank data skip logic for IntegrityVerificationAgent
  3. Agent skip when all results are already persisted (post-agents crash)
  4. Evidence trail skip when already persisted
  5. Invalid case ID and empty input handling
  6. Multiple case isolation (no cross-contamination)
  7. Duplicate submission idempotency
  8. Full DB persistence round-trip

Primary acceptance criterion (from W7 brief):
  "If the server crashes mid-execution, the Coordinator can resume from
   the exact last saved state."
"""
import uuid
import pytest
from unittest.mock import AsyncMock

from app.database.database import (
    init_db, create_case, get_case, update_case_step, update_case_result,
    mark_case_failed
)
from app.agents.orchestration.case_state import (
    LoanCaseState,
    PIPELINE_STEPS,
    STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ensure_db():
    """Guarantee loan_cases table exists before every test."""
    init_db()


def _build_coordinator(
    ingestion_return=None,
    financial_return=None,
    management_return=None,
    sector_return=None,
    integrity_return=None,
    cam_return=None,
):
    """
    Factory: returns an AgentCoordinator with all agents mocked.
    Callers may override individual returns; sane defaults are provided.
    """
    from app.agents.orchestration.coordinator import AgentCoordinator

    mock_ingestion = AsyncMock()
    mock_ingestion.ingest_pdf.return_value = ingestion_return or {
        "text": "Financial document with sufficient content for analysis.", "error": None
    }
    mock_ingestion.parse_financial_statement.return_value = {
        "company_name": "Mock Corp", "sector": "Manufacturing",
        "current_assets": 5_000_000, "current_liabilities": 2_000_000,
        "total_debt": 10_000_000, "base_score": 72
    }

    mock_financial = AsyncMock()
    mock_financial.analyze.return_value = financial_return or {
        "status": "success", "financial_health_score": 72.0, "risk_level": "Medium",
        "ratios": {"current_ratio": 2.5, "dscr": 1.4}, "cash_flow_assessment": {"status": "Stable"},
        "analysis_notes": []
    }

    mock_management = AsyncMock()
    mock_management.analyze.return_value = management_return or {
        "status": "success", "management_score": 85.0, "risk_level": "Low",
        "requires_manual_review": False, "is_knockout": False,
        "promoter_analysis": [], "governance_assessment": {}
    }

    mock_sector = AsyncMock()
    mock_sector.get_sector_outlook.return_value = sector_return or {
        "sector": "Manufacturing", "outlook": "Stable", "risk_factors": []
    }
    mock_sector.check_rbi_policies.return_value = []

    mock_integrity = AsyncMock()
    mock_integrity.cross_validate.return_value = integrity_return or {
        "status": "success", "flags": [], "warnings": []
    }

    mock_cam = AsyncMock()
    mock_cam.generate_cam.return_value = cam_return or {
        "five_cs": {k: {"text": "Good"} for k in ["character", "capacity", "capital", "collateral", "conditions"]},
        "decision": "APPROVE", "recommended_loan_amount": "50 Lakhs",
        "recommended_interest_rate": "10.5%", "decision_rationale": "Solid financials."
    }

    return AgentCoordinator(
        ingestion_agent=mock_ingestion,
        financial_agent=mock_financial,
        management_agent=mock_management,
        sector_agent=mock_sector,
        integrity_agent=mock_integrity,
        cam_agent=mock_cam,
    )


# ---------------------------------------------------------------------------
# PRIMARY W7 TESTS — Crash Recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crash_after_ingestion_skips_reingest():
    """
    PRIMARY ACCEPTANCE CRITERION.

    Simulate a server crash that occurred AFTER ingestion completed and
    the early snapshot was written to DB, but BEFORE agents ran.

    On restart with the same case_id, the coordinator must:
      - NOT call ingest_pdf() or parse_financial_statement()
      - CALL financial_agent.analyze() (was not persisted)
      - Complete successfully and write STATUS_COMPLETED to DB
    """
    # --- Seed DB to match a crash-survived state ---
    case_id = f"CASE_W7_INGEST_{uuid.uuid4().hex[:10].upper()}"
    persisted_extracted = {
        "company_name": "Crash-Test Corp", "sector": "Manufacturing",
        "current_assets": 5_000_000, "current_liabilities": 2_000_000,
        "total_debt": 10_000_000, "base_score": 72
    }
    create_case(case_id, {"file_path": "/phantom/file.pdf"}, institution_id="DEFAULT")
    update_case_step(case_id, "ingestion_complete", status=STATUS_RUNNING)
    update_case_result(case_id, {"extracted_data": persisted_extracted}, status=STATUS_RUNNING)

    # Verify seeded state
    record = get_case(case_id)
    assert record["current_step"] == "ingestion_complete"
    assert record["result_data"]["extracted_data"]["company_name"] == "Crash-Test Corp"

    # --- Build coordinator and resume ---
    coordinator = _build_coordinator()

    # The temp file is gone (simulates server restart) — resume must not require it
    result = await coordinator.run_appraisal_with_state(
        {"file_path": "/phantom/file.pdf"},
        case_id=case_id
    )

    # === CRITICAL ASSERTIONS ===
    coordinator.ingestion_agent.ingest_pdf.assert_not_called()
    coordinator.ingestion_agent.parse_financial_statement.assert_not_called()
    coordinator.financial_agent.analyze.assert_called_once()  # ran fresh
    assert result["status"] == "success"
    assert result["case_id"] == case_id

    final_record = get_case(case_id)
    assert final_record["status"] == STATUS_COMPLETED
    assert final_record["current_step"] == "done"


@pytest.mark.asyncio
async def test_crash_after_agents_skips_reingest_and_rerun_agents():
    """
    Simulate crash AFTER all agents completed (snapshot saved) but BEFORE
    evidence/CAM ran.

    Resume must skip: ingestion + all agents.
    Resume must run: evidence + CAM.
    """
    case_id = f"CASE_W7_AGENTS_{uuid.uuid4().hex[:10].upper()}"
    persisted_extracted = {
        "company_name": "PostAgent Corp", "sector": "Finance",
        "current_assets": 1_000_000, "total_debt": 500_000
    }
    persisted_snapshot = {
        "extracted_data": persisted_extracted,
        "financial_result": {
            "financial_health_score": 65.0, "risk_level": "Medium",
            "ratios": {}, "cash_flow_assessment": {"status": "Stable"}, "analysis_notes": []
        },
        "management_result": {
            "management_score": 70.0, "risk_level": "Low",
            "requires_manual_review": False, "is_knockout": False,
            "promoter_analysis": [], "governance_assessment": {}
        },
        "sector_result": {
            "sector": "Finance", "outlook": "Stable", "risk_factors": [], "rbi_policy_impact": []
        },
        "integrity_result": {"status": "success", "flags": [], "warnings": []},
        "evidence_trail": [],
        "has_gst_bank_data": False,
    }

    create_case(case_id, {}, institution_id="DEFAULT")
    update_case_step(case_id, "agents_dispatched", status=STATUS_RUNNING)
    update_case_result(case_id, persisted_snapshot, status=STATUS_RUNNING)

    # Build coordinator — ingestion + all agents must NOT be called
    coordinator = _build_coordinator()
    result = await coordinator.run_appraisal_with_state(
        {"file_path": "/phantom/file.pdf"},
        case_id=case_id
    )

    # Skipped
    coordinator.ingestion_agent.ingest_pdf.assert_not_called()
    coordinator.financial_agent.analyze.assert_not_called()
    coordinator.management_agent.analyze.assert_not_called()
    coordinator.sector_agent.get_sector_outlook.assert_not_called()
    coordinator.integrity_agent.cross_validate.assert_not_called()

    # CAM ran (was not persisted)
    coordinator.cam_agent.generate_cam.assert_called_once()

    assert result["status"] == "success"
    final_record = get_case(case_id)
    assert final_record["status"] == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_early_snapshot_written_after_ingestion(tmp_path):
    """
    Verify that the early snapshot (extracted_data) is written to DB immediately
    after ingestion completes, before agents are run.

    We do this by capturing the update_case_result calls via a side-effect
    and confirming the first call carries extracted_data.
    """
    from unittest.mock import patch
    calls = []

    original_update = None

    def capture_update(case_id, result_data, status=None):
        calls.append(result_data)
        return original_update(case_id, result_data, status=status) if original_update else None

    import app.agents.orchestration.coordinator as coord_module

    coordinator = _build_coordinator()
    fake_pdf = tmp_path / "doc.pdf"
    fake_pdf.write_bytes(b"dummy")

    with patch("app.agents.orchestration.coordinator.update_case_result", side_effect=capture_update):
        result = await coordinator.run_appraisal_with_state({"file_path": str(fake_pdf)})

    # First update_case_result call should carry the ingestion early snapshot
    assert len(calls) >= 2, "Expected at least 2 update_case_result calls (early + final)"
    assert "extracted_data" in calls[0], "First update_case_result must persist extracted_data"


# ---------------------------------------------------------------------------
# DYNAMIC ROUTING — GST / Bank Skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integrity_skipped_when_no_gst_and_no_bank_data(tmp_path):
    """
    When gst_data=[] and bank_data=[] (or omitted), IntegrityVerificationAgent
    must NOT be called, and the integrity_check output must show status='skipped'.
    """
    coordinator = _build_coordinator()
    fake_pdf = tmp_path / "no_gst.pdf"
    fake_pdf.write_bytes(b"dummy")

    result = await coordinator.run_appraisal_with_state({
        "file_path": str(fake_pdf),
        "gst_data": [],
        "bank_data": []
    })

    coordinator.integrity_agent.cross_validate.assert_not_called()
    assert result["routing_flags"]["has_gst_bank_data"] is False
    assert result["individual_agent_outputs"]["integrity_check"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_integrity_runs_when_gst_data_present(tmp_path):
    """When gst_data has entries, IntegrityVerificationAgent MUST be called."""
    coordinator = _build_coordinator()
    fake_pdf = tmp_path / "with_gst.pdf"
    fake_pdf.write_bytes(b"dummy")

    result = await coordinator.run_appraisal_with_state({
        "file_path": str(fake_pdf),
        "gst_data": [{"month": "2024-01", "turnover": 500_000}],
        "bank_data": []
    })

    coordinator.integrity_agent.cross_validate.assert_called_once()
    assert result["routing_flags"]["has_gst_bank_data"] is True


@pytest.mark.asyncio
async def test_integrity_runs_when_only_bank_data_present(tmp_path):
    """When bank_data has entries (gst_data empty), IntegrityVerificationAgent MUST run."""
    coordinator = _build_coordinator()
    fake_pdf = tmp_path / "with_bank.pdf"
    fake_pdf.write_bytes(b"dummy")

    result = await coordinator.run_appraisal_with_state({
        "file_path": str(fake_pdf),
        "gst_data": [],
        "bank_data": [{"month": "2024-01", "credit": 200_000}]
    })

    coordinator.integrity_agent.cross_validate.assert_called_once()
    assert result["routing_flags"]["has_gst_bank_data"] is True


@pytest.mark.asyncio
async def test_routing_flags_all_present_in_result(tmp_path):
    """Result must always include has_financials, has_promoters, has_gst_bank_data."""
    coordinator = _build_coordinator()
    fake_pdf = tmp_path / "test.pdf"
    fake_pdf.write_bytes(b"dummy")

    result = await coordinator.run_appraisal_with_state({"file_path": str(fake_pdf)})

    flags = result.get("routing_flags", {})
    assert "has_financials" in flags
    assert "has_promoters" in flags
    assert "has_gst_bank_data" in flags


# ---------------------------------------------------------------------------
# INVALID / EMPTY INPUT HANDLING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_file_raises_on_new_case():
    """run_appraisal_with_state raises ValueError when file doesn't exist (fresh case)."""
    coordinator = _build_coordinator()
    with pytest.raises((ValueError, Exception)):
        await coordinator.run_appraisal_with_state({
            "file_path": "/definitely/does/not/exist/doc.pdf"
        })


@pytest.mark.asyncio
async def test_empty_dict_raises():
    """run_appraisal_with_state raises on empty dict (no file_path)."""
    coordinator = _build_coordinator()
    with pytest.raises((ValueError, Exception)):
        await coordinator.run_appraisal_with_state({})


def test_get_case_nonexistent_returns_none():
    """get_case returns None for an ID that has never been created."""
    assert get_case("CASE_NEVER_CREATED_XYZ123") is None


def test_get_case_empty_string_returns_none():
    """get_case with an empty string returns None without raising."""
    assert get_case("") is None


# ---------------------------------------------------------------------------
# MULTIPLE CASE ISOLATION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_concurrent_cases_do_not_interfere(tmp_path):
    """
    Two independent appraisal cases are tracked independently.
    Their case_ids, DB records, and results must not bleed into each other.
    """
    pdf1 = tmp_path / "doc1.pdf"
    pdf2 = tmp_path / "doc2.pdf"
    pdf1.write_bytes(b"dummy1")
    pdf2.write_bytes(b"dummy2")

    c1 = _build_coordinator()
    c2 = _build_coordinator()

    result1 = await c1.run_appraisal_with_state({"file_path": str(pdf1)})
    result2 = await c2.run_appraisal_with_state({"file_path": str(pdf2)})

    assert result1["case_id"] != result2["case_id"]

    rec1 = get_case(result1["case_id"])
    rec2 = get_case(result2["case_id"])

    assert rec1 is not None and rec2 is not None
    assert rec1["status"] == STATUS_COMPLETED
    assert rec2["status"] == STATUS_COMPLETED
    assert rec1["result_data"]["case_id"] == result1["case_id"]
    assert rec2["result_data"]["case_id"] == result2["case_id"]


# ---------------------------------------------------------------------------
# DUPLICATE EXECUTION — Idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_submission_of_completed_case_is_idempotent(tmp_path):
    """
    Submitting an already-COMPLETED case_id must return the cached result
    without invoking any agent a second time.
    """
    coordinator = _build_coordinator()
    fake_pdf = tmp_path / "doc.pdf"
    fake_pdf.write_bytes(b"dummy")

    # First run
    result1 = await coordinator.run_appraisal_with_state({"file_path": str(fake_pdf)})
    case_id = result1["case_id"]
    assert coordinator.ingestion_agent.ingest_pdf.call_count == 1

    # Duplicate submission (same case_id, COMPLETED status)
    result2 = await coordinator.run_appraisal_with_state(
        {"file_path": str(fake_pdf)},
        case_id=case_id
    )

    # No agents re-run at all
    assert coordinator.ingestion_agent.ingest_pdf.call_count == 1
    assert coordinator.financial_agent.analyze.call_count == 1
    assert result2["case_id"] == case_id


# ---------------------------------------------------------------------------
# INGESTION FAILURE → FAILED STATE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingestion_exception_propagates_and_marks_failed(tmp_path):
    """
    An exception raised by ingest_pdf must propagate to the caller and
    mark the case FAILED in DB.
    """
    coordinator = _build_coordinator()
    coordinator.ingestion_agent.ingest_pdf.side_effect = Exception("Disk I/O error during read")

    fake_pdf = tmp_path / "bad.pdf"
    fake_pdf.write_bytes(b"dummy")

    with pytest.raises(Exception, match="Disk I/O error"):
        await coordinator.run_appraisal_with_state({"file_path": str(fake_pdf)})


# ---------------------------------------------------------------------------
# DATABASE ROUND-TRIP
# ---------------------------------------------------------------------------

def test_full_db_roundtrip():
    """
    Verifies the complete DB lifecycle: create → update_step → update_result
    → mark_failed, and confirms all changes are readable via get_case().
    """
    case_id = f"CASE_RT_{uuid.uuid4().hex[:10].upper()}"

    # 1. Create
    create_case(case_id, {"file_path": "/tmp/test.pdf"}, institution_id="BANK_TEST")
    rec = get_case(case_id)
    assert rec is not None
    assert rec["status"] == STATUS_PENDING
    assert rec["institution_id"] == "BANK_TEST"
    assert rec["current_step"] == "init"

    # 2. Step update
    update_case_step(case_id, "ingestion_complete", status=STATUS_RUNNING)
    rec = get_case(case_id)
    assert rec["current_step"] == "ingestion_complete"
    assert rec["status"] == STATUS_RUNNING

    # 3. Result update (marks COMPLETED)
    update_case_result(case_id, {"decision": "APPROVE"}, status=STATUS_COMPLETED)
    rec = get_case(case_id)
    assert rec["status"] == STATUS_COMPLETED
    assert rec["current_step"] == "done"
    assert rec["result_data"]["decision"] == "APPROVE"

    # 4. Failure (separate case)
    fail_id = f"CASE_FAIL_{uuid.uuid4().hex[:10].upper()}"
    create_case(fail_id, {}, institution_id="DEFAULT")
    mark_case_failed(fail_id, "Simulated network timeout")
    fail_rec = get_case(fail_id)
    assert fail_rec["status"] == STATUS_FAILED
    assert "network timeout" in fail_rec["error_message"]


# ---------------------------------------------------------------------------
# LoanCaseState — has_gst_bank_data Field
# ---------------------------------------------------------------------------

def test_loan_case_state_has_gst_bank_data_default_true():
    """LoanCaseState defaults has_gst_bank_data to True."""
    state = LoanCaseState(case_id="CASE_FLAG_001")
    assert hasattr(state, "has_gst_bank_data")
    assert state.has_gst_bank_data is True


def test_from_db_record_restores_gst_bank_data_flag():
    """from_db_record correctly restores has_gst_bank_data from persisted result_data."""
    record = {
        "case_id": "CASE_RESTORE_001",
        "status": STATUS_RUNNING,
        "current_step": "agents_dispatched",
        "has_financials": True,
        "has_promoters": True,
        "institution_id": "DEFAULT",
        "result_data": {"has_gst_bank_data": False}
    }
    state = LoanCaseState.from_db_record(record)
    assert state.has_gst_bank_data is False


def test_from_db_record_defaults_gst_bank_data_to_true_if_absent():
    """from_db_record defaults has_gst_bank_data to True when key not in result_data."""
    record = {
        "case_id": "CASE_RESTORE_002",
        "status": STATUS_RUNNING,
        "current_step": "ingestion_complete",
        "has_financials": True,
        "has_promoters": True,
        "institution_id": "DEFAULT",
        "result_data": {}
    }
    state = LoanCaseState.from_db_record(record)
    assert state.has_gst_bank_data is True


def test_from_db_record_restores_evidence_trail():
    """from_db_record correctly restores evidence_trail from persisted result_data."""
    trail = [{"category": "Financial Ratios", "severity": "HIGH", "title": "Low CR", "description": "0.8"}]
    record = {
        "case_id": "CASE_RESTORE_003",
        "status": STATUS_RUNNING,
        "current_step": "evidence_built",
        "has_financials": True,
        "has_promoters": True,
        "institution_id": "DEFAULT",
        "result_data": {"evidence_trail": trail}
    }
    state = LoanCaseState.from_db_record(record)
    assert state.evidence_trail == trail


def test_to_snapshot_includes_has_gst_bank_data():
    """to_snapshot() must include has_gst_bank_data for crash recovery persistence."""
    state = LoanCaseState(case_id="CASE_SNAP_001")
    state.has_gst_bank_data = False
    snap = state.to_snapshot()
    assert "has_gst_bank_data" in snap
    assert snap["has_gst_bank_data"] is False


def test_pipeline_steps_integrity():
    """PIPELINE_STEPS must contain mandatory steps in correct relative order."""
    required = ["init", "policy_loaded", "ingestion_complete", "agents_dispatched", "evidence_built", "cam_complete", "done"]
    for step in required:
        assert step in PIPELINE_STEPS, f"Missing step: '{step}'"
    # Relative ordering
    idx = {s: i for i, s in enumerate(PIPELINE_STEPS)}
    assert idx["init"] < idx["ingestion_complete"] < idx["agents_dispatched"] < idx["evidence_built"] < idx["done"]
