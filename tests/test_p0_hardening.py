"""Regression tests for P0-2 (provenance), P0-3 (determinism),
P0-4 (fail-closed degraded state) and P0-5 (production database policy).

P0-1 logging is covered separately in tests/test_p0_1_log_safety.py.
"""

import importlib
import json
import sqlite3
import uuid

import pytest

from app.core import decision_config as dc
from app.core import db_policy
from app.core.db_policy import ProductionDatabaseError
from app.core.execution_state import (
    DECISION_ANALYSIS_INCOMPLETE,
    DECISION_MANUAL_REVIEW,
    OPTIONAL_AGENTS,
    REQUIRED_AGENTS,
    AgentStatus,
    AnalysisStatus,
    AppraisalExecution,
    ErrorCode,
    classify_exception,
    gate_decision,
)
from app.core.output_validation import validate, validate_cam, validate_ingestion
from app.core.provenance import AGENT_VERSIONS, PROMPT_VERSIONS, ProvenanceLedger, capture


class FakeLLM:
    def __init__(self, model="openai/gpt-oss-120b", temperature=0.0):
        self.model_name = model
        self.temperature = temperature


# =========================================================================
# P0-2 provenance
# =========================================================================

def test_provenance_captures_model_and_versions():
    prov = capture("document_ingestion", llm=FakeLLM())
    assert prov.agent == "document_ingestion"
    assert prov.provider == "groq"
    assert prov.model_name == "openai/gpt-oss-120b"
    assert prov.prompt_version == PROMPT_VERSIONS["document_ingestion"]
    assert prov.agent_version == AGENT_VERSIONS["document_ingestion"]
    assert prov.temperature == 0.0
    assert prov.generated_at


def test_provenance_is_per_agent_not_one_model_for_whole_appraisal():
    """A fallback rollover must be recorded truthfully, not flattened."""
    led = ProvenanceLedger()
    led.record_capture("document_ingestion", llm=FakeLLM("openai/gpt-oss-120b"))
    led.record_capture("cam_generator", llm=FakeLLM("openai/gpt-oss-20b"))

    entries = {e.agent: e.model_name for e in led.entries}
    assert entries["document_ingestion"] == "openai/gpt-oss-120b"
    assert entries["cam_generator"] == "openai/gpt-oss-20b"

    summary = led.summary()
    assert summary["model_name"] == "MULTIPLE"
    assert summary["models_used"] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_provenance_never_invents_values_when_llm_unknown():
    prov = capture("sector_context", llm=None)
    assert prov.provider is None
    assert prov.model_name is None
    assert prov.temperature is None
    # Static, code-derived values are legitimately known.
    assert prov.prompt_version == PROMPT_VERSIONS["sector_context"]


def test_provenance_capture_never_raises():
    class Hostile:
        @property
        def model_name(self):
            raise RuntimeError("boom")

    prov = capture("cam_generator", llm=Hostile())
    assert prov.agent == "cam_generator"


def test_provenance_columns_exist_and_persist(tmp_path, monkeypatch):
    from app.database import database as db

    monkeypatch.setenv("APP_ENV", "test")
    db_file = tmp_path / "prov.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.init_db()

    conn = sqlite3.connect(db_file)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(appraisal_records)")}
    for expected in (
        "model_provider", "model_name", "model_version", "prompt_version",
        "agent_version", "temperature", "provider_request_id",
        "agent_provenance", "analysis_status", "degraded_components",
        "decision_allowed", "provenance_recorded_at",
    ):
        assert expected in cols, f"missing provenance column {expected}"
    conn.close()


def test_historical_records_keep_null_provenance(tmp_path, monkeypatch):
    """Rows written before provenance existed must read NULL, never fabricated."""
    from app.database import database as db

    monkeypatch.setenv("APP_ENV", "test")
    db_file = tmp_path / "hist.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.init_db()

    conn = sqlite3.connect(db_file)
    legacy_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO appraisal_records (id, company_id, decision) VALUES (?, ?, ?)",
        (legacy_id, "COMP_LEGACY", "APPROVE"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT model_name, prompt_version, agent_provenance FROM appraisal_records WHERE id = ?",
        (legacy_id,),
    ).fetchone()
    assert row == (None, None, None), "historical provenance must remain NULL"
    conn.close()


# =========================================================================
# P0-3 determinism configuration
# =========================================================================

def test_decision_path_temperature_is_zero():
    assert dc.DECISION_PATH_TEMPERATURE == 0.0


@pytest.mark.parametrize("agent", sorted(dc.DECISION_PATH_AGENTS))
def test_every_decision_agent_uses_zero_temperature(agent):
    assert dc.temperature_for(agent) == 0.0
    assert dc.is_decision_path(agent)


def test_no_agent_source_file_hardcodes_nonzero_temperature():
    """Guards against a future edit reintroducing sampling on the decision path."""
    from pathlib import Path

    agents_dir = Path(__file__).resolve().parents[1] / "app" / "agents"
    offenders = []
    for path in agents_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in ("temperature=0.1", "temperature=0.2", "temperature=0.3",
                       "temperature=0.5", "temperature=0.7", "temperature=1"):
            if marker in text:
                offenders.append(f"{path.name}:{marker}")
    assert not offenders, f"non-zero temperature on decision path: {offenders}"


def test_determinism_is_not_overclaimed():
    """The documented guarantee must stay honest about provider behaviour."""
    text = dc.describe()["determinism_guarantee"].lower()
    assert "not a provider determinism guarantee" in text


# =========================================================================
# P0-4 fail-closed degraded state
# =========================================================================

def test_required_and_optional_agents_are_disjoint():
    assert not (REQUIRED_AGENTS & OPTIONAL_AGENTS)


def test_all_required_success_allows_decision():
    ex = AppraisalExecution()
    for agent in REQUIRED_AGENTS:
        ex.record_success(agent)
    assert ex.decision_allowed
    assert ex.status is AnalysisStatus.COMPLETED


def test_required_agent_failure_blocks_decision():
    ex = AppraisalExecution()
    ex.record_success("document_ingestion")
    ex.record_success("cam_generator")
    ex.record_failure("financial_health", ErrorCode.MODEL_TIMEOUT.value, retryable=True)

    assert ex.decision_allowed is False
    assert ex.status is AnalysisStatus.FAILED
    assert "financial_health" in ex.missing_required


def test_required_agent_that_never_ran_blocks_decision():
    """Silence is not success - a required agent that never reported blocks."""
    ex = AppraisalExecution()
    ex.record_success("document_ingestion")
    assert ex.decision_allowed is False
    assert "cam_generator" in ex.missing_required
    assert "financial_health" in ex.missing_required


def test_optional_agent_failure_degrades_but_allows_decision():
    ex = AppraisalExecution()
    for agent in REQUIRED_AGENTS:
        ex.record_success(agent)
    ex.record_failure("risk_intelligence", ErrorCode.MODEL_RATE_LIMITED.value, retryable=True)
    ex.record_degraded("sector_context", ErrorCode.EXTERNAL_RESEARCH_UNAVAILABLE.value)

    assert ex.decision_allowed is True
    assert ex.status is AnalysisStatus.DEGRADED
    assert ex.degraded_components == ["risk_intelligence", "sector_context"]
    # The failure stays visible and auditable.
    codes = {r.agent: r.error_code for r in ex.results}
    assert codes["risk_intelligence"] == ErrorCode.MODEL_RATE_LIMITED.value


def test_failed_required_never_becomes_manual_review():
    """The central P0-4 rule: a system failure is not an underwriting decision."""
    ex = AppraisalExecution()
    ex.record_success("document_ingestion")
    ex.record_failure("cam_generator", ErrorCode.MODEL_UNAVAILABLE.value)
    ex.record_success("financial_health")

    gated = gate_decision(ex, "MANUAL REVIEW")
    assert gated["decision"] == DECISION_ANALYSIS_INCOMPLETE
    assert gated["decision"] != DECISION_MANUAL_REVIEW
    assert gated["decision"] != "MANUAL REVIEW"
    assert gated["decision_allowed"] is False
    assert gated["recommended_loan_amount"] == "UNAVAILABLE"
    assert "cam_generator" in gated["missing_required"]
    assert "system failure" in gated["decision_rationale"].lower()


def test_valid_decision_passes_through_gate_unchanged():
    ex = AppraisalExecution()
    for agent in REQUIRED_AGENTS:
        ex.record_success(agent)
    gated = gate_decision(ex, "APPROVE")
    assert gated["decision"] == "APPROVE"
    assert gated["decision_allowed"] is True


def test_security_block_yields_blocked_status():
    ex = AppraisalExecution()
    for agent in REQUIRED_AGENTS:
        ex.record_success(agent)
    ex.security_blocked = True
    assert ex.status is AnalysisStatus.BLOCKED
    assert ex.decision_allowed is False


@pytest.mark.parametrize("message,expected,retryable", [
    ("Error code: 429 - rate limit reached", ErrorCode.MODEL_RATE_LIMITED.value, True),
    ("Request timeout after 60s", ErrorCode.MODEL_TIMEOUT.value, True),
    ("Error code: 413 - request too large", ErrorCode.MODEL_UNAVAILABLE.value, False),
    ("connection refused", ErrorCode.MODEL_UNAVAILABLE.value, True),
])
def test_classify_exception(message, expected, retryable):
    code, is_retryable = classify_exception(RuntimeError(message))
    assert code == expected
    assert is_retryable is retryable


# ---- P0-4A false-success regression ------------------------------------

def test_the_original_false_success_is_now_rejected():
    """Reproduce Unknown Entity + fabricated 65 + MANUAL REVIEW end to end."""
    ingestion_payload = {
        "company_name": "Unknown Entity",
        "sector": "Unknown",
        "total_revenue": None,
        "total_debt": None,
        "shareholder_equity": None,
        "current_assets": None,
        "current_liabilities": None,
        "base_score": 65,
        "qualitative_notes": "Document could not be fully processed.",
    }
    status, code, reason = validate_ingestion(ingestion_payload)
    assert status is AgentStatus.FAILED, reason
    assert code == ErrorCode.INVALID_OUTPUT.value

    cam_payload = {
        "document_control": {"borrower_name": "Unknown", "status": "ERROR"},
        "five_cs": {
            "character": "N/A", "capacity": "N/A", "capital": "N/A",
            "collateral": "N/A", "conditions": "N/A",
        },
    }
    cam_status, _, _ = validate_cam(cam_payload)
    assert cam_status is AgentStatus.FAILED

    ex = AppraisalExecution()
    ex.record_failure("document_ingestion", code, reason)
    ex.record_failure("cam_generator", ErrorCode.INVALID_OUTPUT.value)
    ex.record_success("financial_health")

    gated = gate_decision(ex, "MANUAL REVIEW")
    assert gated["decision_allowed"] is False
    assert gated["decision"] == DECISION_ANALYSIS_INCOMPLETE


def test_degraded_extraction_flag_fails_required_agent():
    status, _, _ = validate_ingestion({
        "company_name": "Real Borrower Ltd",
        "total_revenue": 1000,
        "extraction_degraded": True,
    })
    assert status is AgentStatus.FAILED


def test_valid_extraction_passes_validation():
    status, code, reason = validate_ingestion({
        "company_name": "Anantara Agro Foods Pvt Ltd",
        "sector": "Agriculture",
        "total_revenue": 42000000,
        "total_debt": 18200000,
        "shareholder_equity": 11750000,
        "base_score": 90,
        "extraction_degraded": False,
    })
    assert status is AgentStatus.SUCCESS, f"{code}: {reason}"


def test_populated_cam_passes_validation():
    status, code, reason = validate_cam({
        "document_control": {"status": "PENDING"},
        "five_cs": {
            "character": {"evidence": "CMR-4", "assessment": "Clean record", "risk_implication": "Low"},
            "capacity": {"evidence": "Rev 42m vs debt 18m", "assessment": "Adequate", "risk_implication": "Low"},
            "capital": {"evidence": "Equity 11.7m", "assessment": "Moderate", "risk_implication": "Medium"},
            "collateral": {"evidence": "None", "assessment": "Unsecured", "risk_implication": "High"},
            "conditions": {"evidence": "Agri sector", "assessment": "Stable", "risk_implication": "Medium"},
        },
    })
    assert status is AgentStatus.SUCCESS, f"{code}: {reason}"


def test_security_blocked_document_is_blocked_not_failed():
    status, code, _ = validate_ingestion({
        "company_name": "Unknown Entity",
        "security": {"status": "REJECTED", "prompt_injection": True},
    })
    assert status is AgentStatus.BLOCKED
    assert code == ErrorCode.SECURITY_BLOCKED.value


def test_optional_research_unavailable_degrades_only():
    status, code, _ = validate("realtime_intelligence", {"research_degraded": True})
    assert status is AgentStatus.DEGRADED
    assert code == ErrorCode.EXTERNAL_RESEARCH_UNAVAILABLE.value


# =========================================================================
# P0-5 production database policy
# =========================================================================

def test_development_allows_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    assert db_policy.sqlite_allowed()
    assert db_policy.enforce_database_policy() == "sqlite"


def test_test_environment_allows_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    assert db_policy.enforce_database_policy() == "sqlite"


def test_production_without_database_fails_fast(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    with pytest.raises(ProductionDatabaseError) as exc:
        db_policy.enforce_database_policy()
    assert "production" in str(exc.value).lower()


def test_production_with_database_uses_supabase(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "service-role-key")
    assert db_policy.enforce_database_policy() == "supabase"
    assert not db_policy.sqlite_allowed()


def test_production_cannot_open_sqlite_connection(monkeypatch):
    """Even a direct call to the SQLite factory is refused in production."""
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ProductionDatabaseError):
        db_policy.assert_sqlite_permitted("unit-test")


def test_unset_environment_defaults_to_development(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert db_policy.current_environment() == db_policy.DEVELOPMENT
    assert db_policy.sqlite_allowed()
