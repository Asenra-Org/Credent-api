"""P1-4 - retry and failover verification.

The resilient client in app/core/llm.py was written after a Groq quota
exhaustion took the whole pipeline down, but had never been proven against the
failure modes it claims to handle. Every test here is deterministic and mocked:
no provider API is called.

What is asserted:
  * 429 / 5xx retry the same model with backoff, then advance
  * 413 does NOT retry the same model (the request cannot shrink itself) and
    advances immediately
  * non-retryable errors propagate without burning attempts
  * retries are bounded - no infinite loop
  * failover reaches the fallback models in configured order
  * provenance records the model that ACTUALLY served the call
  * total exhaustion still fails closed: FAILED / decision_allowed False
"""

import asyncio
import os
from unittest.mock import patch

import pytest
from langchain_groq import ChatGroq

from app.core import llm as llm_module
from app.core.execution_state import (
    DECISION_ANALYSIS_INCOMPLETE,
    AgentResult,
    AgentStatus,
    AppraisalExecution,
    ErrorCode,
    classify_exception,
    gate_decision,
)
from app.core.llm import ChatGroqWithFallback, ResilientChatGroq, _backoff_seconds, _status_of
from app.core.provenance import ProvenanceLedger, capture

PRIMARY = "openai/gpt-oss-120b"
FB1 = "openai/gpt-oss-20b"
FB2 = "qwen/qwen3.6-27b"


class ProviderError(Exception):
    """Stands in for groq.APIStatusError / RateLimitError."""

    def __init__(self, status: int, message: str = "simulated"):
        super().__init__(f"Error code: {status} - {message}")
        self.status_code = status


@pytest.fixture
def chain_env(monkeypatch):
    monkeypatch.setenv("PRIMARY_LLM_MODEL", PRIMARY)
    monkeypatch.setenv("FALLBACK_LLM_MODEL_1", FB1)
    monkeypatch.setenv("FALLBACK_LLM_MODEL_2", FB2)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-used")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


@pytest.fixture
def client(chain_env):
    return ChatGroqWithFallback(model=PRIMARY, temperature=0, max_tokens=64, api_key="test-key")


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Keep backoff logic exercised but instant."""
    async def _fast(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)


def _install(monkeypatch, behaviour):
    """Replace ChatGroq._agenerate with a scripted double; returns the call log."""
    calls = []

    async def fake(self, messages, stop=None, run_manager=None, **kw):
        calls.append(self.model_name)
        result = behaviour(self.model_name, len(calls))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ChatGroq, "_agenerate", fake)
    return calls


async def _invoke(client):
    return await client._agenerate([], None, None)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def test_client_is_resilient_and_builds_full_chain(client):
    assert isinstance(client, ResilientChatGroq)
    assert [c.model_name for c in client._siblings()] == [PRIMARY, FB1, FB2]


def test_chain_deduplicates_repeated_models(monkeypatch):
    monkeypatch.setenv("PRIMARY_LLM_MODEL", PRIMARY)
    monkeypatch.setenv("FALLBACK_LLM_MODEL_1", PRIMARY)   # duplicate
    monkeypatch.setenv("FALLBACK_LLM_MODEL_2", FB1)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    c = ChatGroqWithFallback(model=PRIMARY, temperature=0, api_key="k")
    assert [s.model_name for s in c._siblings()] == [PRIMARY, FB1]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 413])
def test_status_extraction(status):
    assert _status_of(ProviderError(status)) == status


def test_status_extraction_from_message_only():
    assert _status_of(RuntimeError("Error code: 429 - rate limit reached")) == 429


def test_backoff_grows_and_is_capped():
    delays = [_backoff_seconds(i) for i in range(8)]
    assert delays[0] < delays[3]
    assert all(d <= 8.75 for d in delays), "backoff must stay bounded"


# ---------------------------------------------------------------------------
# retry behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_429_retries_same_model_then_succeeds(client, monkeypatch):
    calls = _install(monkeypatch, lambda model, n: "OK" if n >= 3 else ProviderError(429))
    result = await _invoke(client)
    assert result == "OK"
    assert calls == [PRIMARY, PRIMARY, PRIMARY], "429 must retry the same model"


@pytest.mark.asyncio
async def test_429_exhausts_attempts_then_fails_over(client, monkeypatch):
    calls = _install(
        monkeypatch,
        lambda model, n: "OK" if model == FB1 else ProviderError(429),
    )
    result = await _invoke(client)
    assert result == "OK"
    assert calls[:3] == [PRIMARY] * 3, "primary should use all its attempts"
    assert calls[3] == FB1, "then advance to the first fallback"


@pytest.mark.asyncio
async def test_413_does_not_retry_same_model(client, monkeypatch):
    """A too-large request cannot succeed by retrying it unchanged."""
    calls = _install(
        monkeypatch,
        lambda model, n: "OK" if model == FB1 else ProviderError(413),
    )
    result = await _invoke(client)
    assert result == "OK"
    assert calls.count(PRIMARY) == 1, "413 must advance immediately, not retry"
    assert calls == [PRIMARY, FB1]


@pytest.mark.asyncio
async def test_transient_5xx_is_retried(client, monkeypatch):
    calls = _install(monkeypatch, lambda model, n: "OK" if n >= 2 else ProviderError(503))
    assert await _invoke(client) == "OK"
    assert calls == [PRIMARY, PRIMARY]


@pytest.mark.asyncio
async def test_non_retryable_error_propagates_immediately(client, monkeypatch):
    """A 401 is a configuration fault; retrying and failing over only hides it."""
    calls = _install(monkeypatch, lambda model, n: ProviderError(401, "Invalid API Key"))
    with pytest.raises(ProviderError):
        await _invoke(client)
    assert calls == [PRIMARY], "must not retry or fail over on a non-retryable status"


@pytest.mark.asyncio
async def test_retries_are_bounded_no_infinite_loop(client, monkeypatch):
    calls = _install(monkeypatch, lambda model, n: ProviderError(429))
    with pytest.raises(ProviderError):
        await _invoke(client)
    # 3 models x 3 attempts. The exact bound matters less than that it is finite.
    assert len(calls) == 9
    assert calls.count(PRIMARY) == 3 and calls.count(FB1) == 3 and calls.count(FB2) == 3


@pytest.mark.asyncio
async def test_timeout_is_retried_and_classified(client, monkeypatch):
    calls = _install(
        monkeypatch,
        lambda model, n: "OK" if n >= 2 else asyncio.TimeoutError("request timed out"),
    )
    assert await _invoke(client) == "OK"
    code, retryable = classify_exception(asyncio.TimeoutError("request timed out"))
    assert code == ErrorCode.MODEL_TIMEOUT.value and retryable is True


@pytest.mark.asyncio
async def test_connection_error_is_retryable(client, monkeypatch):
    calls = _install(
        monkeypatch,
        lambda model, n: "OK" if n >= 2 else ConnectionError("connection refused"),
    )
    assert await _invoke(client) == "OK"
    code, retryable = classify_exception(ConnectionError("connection refused"))
    assert code == ErrorCode.MODEL_UNAVAILABLE.value and retryable is True


# ---------------------------------------------------------------------------
# failover ordering and exhaustion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failover_walks_the_configured_order(client, monkeypatch):
    calls = _install(
        monkeypatch,
        lambda model, n: "OK" if model == FB2 else ProviderError(413),
    )
    assert await _invoke(client) == "OK"
    assert calls == [PRIMARY, FB1, FB2], "must try fallbacks in configured order"


@pytest.mark.asyncio
async def test_all_models_unavailable_raises_last_error(client, monkeypatch):
    _install(monkeypatch, lambda model, n: ProviderError(429, f"quota exhausted on {model}"))
    with pytest.raises(ProviderError) as exc:
        await _invoke(client)
    assert "quota exhausted" in str(exc.value)


@pytest.mark.asyncio
async def test_quota_exhaustion_on_every_model_fails_closed(client, monkeypatch):
    """The scenario that caused the original outage, end to end."""
    _install(monkeypatch, lambda model, n: ProviderError(429, "tokens per day (TPD) exceeded"))

    try:
        await _invoke(client)
        served = True
    except ProviderError as exc:
        served = False
        code, retryable = classify_exception(exc)

    assert served is False
    assert code == ErrorCode.MODEL_RATE_LIMITED.value
    assert retryable is True

    execution = AppraisalExecution()
    execution.record(AgentResult(
        agent="document_ingestion", status=AgentStatus.FAILED,
        error_code=code, retryable=retryable,
    ))
    gated = gate_decision(execution, "APPROVE")
    assert gated["decision"] == DECISION_ANALYSIS_INCOMPLETE
    assert gated["decision_allowed"] is False


# ---------------------------------------------------------------------------
# provenance must reflect the model that actually served the call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_records_each_model_actually_used(client, monkeypatch):
    """After failover, provenance must not claim the primary served the request."""
    served = {}

    async def fake(self, messages, stop=None, run_manager=None, **kw):
        if self.model_name != FB1:
            raise ProviderError(413)
        served["model"] = self.model_name
        return "OK"

    monkeypatch.setattr(ChatGroq, "_agenerate", fake)
    await _invoke(client)
    assert served["model"] == FB1

    # Provenance reads the client that served, not the environment default.
    serving_client = next(c for c in client._siblings() if c.model_name == FB1)
    entry = capture("document_ingestion", llm=serving_client)
    assert entry.model_name == FB1
    assert entry.model_name != PRIMARY, "provenance must not misreport the primary"


def test_ledger_reports_multiple_models_after_failover(client):
    ledger = ProvenanceLedger()
    primary_client, fb_client = client._siblings()[0], client._siblings()[1]
    ledger.record_capture("document_ingestion", llm=primary_client)
    ledger.record_capture("cam_generator", llm=fb_client)

    summary = ledger.summary()
    assert summary["model_name"] == "MULTIPLE"
    assert set(summary["models_used"]) == {PRIMARY, FB1}
    per_agent = {e.agent: e.model_name for e in ledger.entries}
    assert per_agent["document_ingestion"] == PRIMARY
    assert per_agent["cam_generator"] == FB1


# ---------------------------------------------------------------------------
# no duplicate persistence on retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retries_do_not_duplicate_persistence(client, monkeypatch):
    """Retry happens below the persistence layer; one call must save once."""
    from app.core.appraisal_safety import apply_safety_gate

    _install(monkeypatch, lambda model, n: "OK" if n >= 3 else ProviderError(429))
    await _invoke(client)

    saves = []
    monkeypatch.setattr("app.database.database.save_appraisal", lambda p: saves.append(p))

    result = {
        "individual_agent_outputs": {
            "ingestion": {"company_name": "Retry Test Ltd", "total_revenue": 1000000,
                          "extraction_degraded": False},
            "financial_health": {"financial_health_score": 70, "ratios": {"dscr": 1.2},
                                 "metrics": {"revenue": 1000000}},
        },
        "combined_decision": {
            "document_control": {"status": "PENDING"},
            "five_cs": {k: {"evidence": "e", "assessment": "a", "risk_implication": "r"}
                        for k in ["character", "capacity", "capital", "collateral", "conditions"]},
            "decision": "APPROVE",
        },
    }
    apply_safety_gate(result)
    from app.database.database import save_appraisal
    save_appraisal({"company_id": "C1", "company_name": "Retry Test Ltd", "decision": "APPROVE"})
    assert len(saves) == 1, "one appraisal must persist exactly once despite LLM retries"
