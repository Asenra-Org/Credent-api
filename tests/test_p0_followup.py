"""P0 follow-up regression tests.

1. The Celery/worker path enforces the same gating as the API route.
2. document_ingestion provenance records a genuine model, not None.
4. Persistence semantics: incomplete appraisals are kept for audit but can never
   carry a valid credit decision.
"""

import sqlite3
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.core.appraisal_safety import (
    OUTPUT_KEY_TO_AGENT,
    apply_safety_gate,
    build_execution,
    capture_provenance,
    collect_agent_payloads,
    persistence_fields,
)
from app.core.execution_state import (
    DECISION_ANALYSIS_INCOMPLETE,
    REQUIRED_AGENTS,
    AnalysisStatus,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _healthy_result():
    return {
        "individual_agent_outputs": {
            "ingestion": {
                "company_name": "Anantara Agro Foods Pvt Ltd",
                "total_revenue": 42000000, "total_debt": 18200000,
                "shareholder_equity": 11750000, "base_score": 90,
                "extraction_degraded": False,
            },
            "financial_health": {
                "financial_health_score": 88,
                "ratios": {"dscr": 1.4},
                "metrics": {"revenue": 42000000},
            },
            "risk_intelligence": {"adjusted_score": 88},
            "sector_context": {"sector": "Agriculture"},
            "management_quality": {"management_score": 70},
            "web_research": {"news": []},
            "integrity_check": {"turnover_match": True},
        },
        "combined_decision": {
            "document_control": {"status": "PENDING"},
            "five_cs": {k: {"evidence": "e", "assessment": "a", "risk_implication": "r"}
                        for k in ["character", "capacity", "capital", "collateral", "conditions"]},
            "decision": "APPROVE",
            "recommended_loan_amount": "10000000",
            "recommended_interest_rate": "12%",
            "decision_rationale": "Sound financials.",
        },
    }


class _FakeLLM:
    model_name = "openai/gpt-oss-120b"
    temperature = 0.0


class _FakeAgent:
    def __init__(self):
        self.llm = _FakeLLM()


class _FakeCoordinator:
    def __init__(self):
        self.ingestion_agent = _FakeAgent()
        self.cam_agent = _FakeAgent()
        self.financial_agent = _FakeAgent()
        self.sector_agent = _FakeAgent()
        self.management_agent = _FakeAgent()


# ---------------------------------------------------------------------------
# 1. shared safety path - route and worker behave identically
# ---------------------------------------------------------------------------

def test_output_key_mapping_covers_required_agents():
    mapped = set(OUTPUT_KEY_TO_AGENT.values()) | {"cam_generator"}
    assert REQUIRED_AGENTS <= mapped


def test_collect_agent_payloads_maps_coordinator_keys():
    payloads = collect_agent_payloads(_healthy_result())
    assert payloads["document_ingestion"]["company_name"].startswith("Anantara")
    assert payloads["realtime_intelligence"] == {"news": []}
    assert payloads["cam_generator"]["decision"] == "APPROVE"


def test_healthy_appraisal_allows_decision():
    result = _healthy_result()
    summary, _, _ = apply_safety_gate(result, coordinator=_FakeCoordinator())
    assert summary["decision_allowed"] is True
    assert summary["analysis_status"] == AnalysisStatus.COMPLETED.value
    assert result["combined_decision"]["decision"] == "APPROVE"


def test_required_failure_blocks_decision_via_shared_path():
    result = _healthy_result()
    result["individual_agent_outputs"]["financial_health"] = None
    summary, _, _ = apply_safety_gate(result, coordinator=_FakeCoordinator())

    assert summary["decision_allowed"] is False
    assert summary["analysis_status"] == AnalysisStatus.FAILED.value
    assert "financial_health" in summary["missing_required"]
    assert result["combined_decision"]["decision"] == DECISION_ANALYSIS_INCOMPLETE
    assert result["combined_decision"]["recommended_loan_amount"] == "UNAVAILABLE"


def test_route_and_worker_produce_identical_gating():
    """The whole point of the shared module: no drift between entry points."""
    route_result = _healthy_result()
    route_result["individual_agent_outputs"]["ingestion"] = {
        "company_name": "Unknown Entity", "base_score": 65, "total_revenue": None,
    }
    worker_result = _healthy_result()
    worker_result["individual_agent_outputs"]["ingestion"] = {
        "company_name": "Unknown Entity", "base_score": 65, "total_revenue": None,
    }

    route_summary, _, _ = apply_safety_gate(
        route_result, ingestion_agent=_FakeAgent(), coordinator=_FakeCoordinator())
    worker_summary, _, _ = apply_safety_gate(
        worker_result, coordinator=_FakeCoordinator())

    assert route_summary["decision_allowed"] == worker_summary["decision_allowed"] is False
    assert route_summary["analysis_status"] == worker_summary["analysis_status"]
    assert route_summary["missing_required"] == worker_summary["missing_required"]
    assert (route_result["combined_decision"]["decision"]
            == worker_result["combined_decision"]["decision"]
            == DECISION_ANALYSIS_INCOMPLETE)


def test_worker_persist_applies_gate(monkeypatch):
    """_persist_appraisal must gate before writing, not after."""
    from app.services import appraisal_worker

    captured = {}

    def fake_save(payload):
        captured.update(payload)

    monkeypatch.setattr("app.database.database.save_appraisal", fake_save)

    result = _healthy_result()
    result["individual_agent_outputs"]["ingestion"] = {
        "company_name": "Unknown Entity", "base_score": 65, "total_revenue": None,
    }
    appraisal_worker._persist_appraisal(
        result, case_id="CASE-WORKER-1", institution_id="DEFAULT",
        coordinator=_FakeCoordinator(),
    )

    assert captured, "save_appraisal was not called"
    assert captured["decision_allowed"] is False
    assert captured["analysis_status"] == AnalysisStatus.FAILED.value
    assert captured["decision"] == DECISION_ANALYSIS_INCOMPLETE
    assert captured["recommended_loan_amount"] == "UNAVAILABLE"


def test_worker_persist_records_provenance(monkeypatch):
    from app.services import appraisal_worker

    captured = {}
    monkeypatch.setattr("app.database.database.save_appraisal", lambda p: captured.update(p))

    appraisal_worker._persist_appraisal(
        _healthy_result(), case_id="CASE-WORKER-2", institution_id="DEFAULT",
        coordinator=_FakeCoordinator(),
    )
    assert captured["provenance_summary"]["model_name"] == "openai/gpt-oss-120b"
    agents = {e["agent"] for e in captured["agent_provenance"]}
    assert {"document_ingestion", "cam_generator"} <= agents


# ---------------------------------------------------------------------------
# 2. document_ingestion provenance
# ---------------------------------------------------------------------------

def test_ingestion_provenance_records_real_model_from_agent():
    ledger = capture_provenance(ingestion_agent=_FakeAgent(), coordinator=_FakeCoordinator())
    entry = next(e for e in ledger.entries if e.agent == "document_ingestion")
    assert entry.model_name == "openai/gpt-oss-120b"
    assert entry.provider == "groq"
    assert entry.prompt_version is not None


def test_ingestion_provenance_resolves_from_coordinator_when_agent_absent():
    """The worker path has no separate ingestion_agent handle."""
    ledger = capture_provenance(coordinator=_FakeCoordinator())
    entry = next(e for e in ledger.entries if e.agent == "document_ingestion")
    assert entry.model_name == "openai/gpt-oss-120b", "worker path lost the model handle"


def test_provenance_records_null_when_genuinely_unavailable():
    """No handle anywhere means NULL - never an invented value."""
    ledger = capture_provenance()
    entry = next(e for e in ledger.entries if e.agent == "document_ingestion")
    assert entry.model_name is None
    assert entry.provider is None
    assert entry.prompt_version is not None  # code-derived, legitimately known


def test_provenance_covers_more_than_two_agents():
    ledger = capture_provenance(ingestion_agent=_FakeAgent(), coordinator=_FakeCoordinator())
    agents = {e.agent for e in ledger.entries}
    assert {"document_ingestion", "cam_generator", "financial_health"} <= agents


# ---------------------------------------------------------------------------
# 4. persistence semantics
# ---------------------------------------------------------------------------

def test_persistence_fields_carry_state_and_provenance():
    result = _healthy_result()
    summary, prov, ledger = apply_safety_gate(result, coordinator=_FakeCoordinator())
    fields = persistence_fields(summary, prov, ledger)
    for key in ("provenance_summary", "agent_provenance", "analysis_status",
                "degraded_components", "decision_allowed"):
        assert key in fields


def test_incomplete_appraisal_is_still_persisted_for_audit(tmp_path, monkeypatch):
    """Failed runs are kept - the audit trail matters - but clearly marked."""
    from app.database import database as db

    monkeypatch.setenv("APP_ENV", "test")
    db_file = tmp_path / "persist.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.init_db()

    result = _healthy_result()
    result["individual_agent_outputs"]["financial_health"] = None
    summary, prov, ledger = apply_safety_gate(result, coordinator=_FakeCoordinator())

    db.save_appraisal({
        "company_id": "COMP_INCOMPLETE",
        "company_name": "Anantara Agro Foods Pvt Ltd",
        "revenue": 0.0, "debt": 0.0, "base_score": 0, "adjusted_score": 0,
        "decision": result["combined_decision"]["decision"],
        "recommended_loan_amount": result["combined_decision"]["recommended_loan_amount"],
        "recommended_interest_rate": result["combined_decision"]["recommended_interest_rate"],
        "decision_rationale": result["combined_decision"]["decision_rationale"],
        "cam_report": {}, "web_research": {}, "integrity_flags": {},
        "raw_document_data": {}, "financial_ratios": {}, "institution_id": "DEFAULT",
        **persistence_fields(summary, prov, ledger),
    })

    conn = sqlite3.connect(db_file)
    row = conn.execute(
        "SELECT decision, decision_allowed, analysis_status, recommended_loan_amount "
        "FROM appraisal_records ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None, "incomplete appraisal must still be persisted"
    assert row[0] == DECISION_ANALYSIS_INCOMPLETE
    assert row[1] == 0, "decision_allowed must be false"
    assert row[2] == AnalysisStatus.FAILED.value
    assert row[3] == "UNAVAILABLE"


@pytest.mark.parametrize("valid_decision", ["APPROVE", "REJECT", "MANUAL REVIEW"])
def test_incomplete_analysis_never_persists_a_valid_decision(valid_decision):
    """Whatever the CAM proposed, a required failure overwrites it."""
    result = _healthy_result()
    result["combined_decision"]["decision"] = valid_decision
    result["individual_agent_outputs"]["ingestion"] = None

    summary, _, _ = apply_safety_gate(result, coordinator=_FakeCoordinator())
    assert summary["decision_allowed"] is False
    assert result["combined_decision"]["decision"] == DECISION_ANALYSIS_INCOMPLETE
    assert result["combined_decision"]["decision"] != valid_decision


def test_analysis_incomplete_is_distinct_from_all_credit_decisions():
    for decision in ("APPROVE", "REJECT", "MANUAL REVIEW", "MANUAL_REVIEW_REQUIRED"):
        assert DECISION_ANALYSIS_INCOMPLETE != decision


def test_degraded_optional_appraisal_still_persists_a_valid_decision():
    result = _healthy_result()
    result["individual_agent_outputs"]["risk_intelligence"] = None
    summary, _, _ = apply_safety_gate(result, coordinator=_FakeCoordinator())

    assert summary["decision_allowed"] is True
    assert summary["analysis_status"] == AnalysisStatus.DEGRADED.value
    assert "risk_intelligence" in summary["degraded_components"]
    assert result["combined_decision"]["decision"] == "APPROVE"
