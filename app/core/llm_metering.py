# =============================================================================
# CRESEM - Per-call LLM accounting
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
"""Record what every provider call actually consumed.

Until now the platform console reported token usage and cost as "not measured",
which was honest but left two things impossible: pricing a licence, and knowing
which agent is expensive. An account was exhausted in roughly eleven appraisals
and nothing in the system could say where it went.

Three rules govern this module.

Never break an appraisal. Metering is bookkeeping, not a gate. Every write is
wrapped and every failure is swallowed with a printed warning - a credit
appraisal must never fail because its accounting row could not be inserted.

Never record borrower data. Only counts, model names and outcomes are written.
No prompt, no completion, no financial figure ever reaches this table, which is
what [P0-1] requires of anything that touches the LLM path.

Never invent a number. A provider that returns no usage block is recorded with
NULL token counts and a note saying so, rather than an estimate. A cost figure
is derived only where a price is configured; otherwise it stays NULL and the
console keeps saying it is not measured.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Price per million tokens, by model, read from the environment because a
# provider's price list is not a fact the codebase can know. Format:
#   LLM_PRICE_PER_MTOK="sarvam-105b:input=30,output=90"
# Anything unconfigured yields a NULL cost rather than a guess.
_PRICE_ENV = "LLM_PRICE_PER_MTOK"

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS llm_call_log (
    id TEXT PRIMARY KEY,
    case_id TEXT,
    institution_id TEXT,
    agent TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    reasoning_chars INTEGER,
    finish_reason TEXT,
    succeeded INTEGER NOT NULL DEFAULT 1,
    error_kind TEXT,
    latency_ms INTEGER,
    estimated_cost_inr REAL,
    created_at TEXT
)
"""

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS llm_call_log (
    id TEXT PRIMARY KEY,
    case_id TEXT,
    institution_id TEXT,
    agent TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    reasoning_chars INTEGER,
    finish_reason TEXT,
    succeeded INTEGER NOT NULL DEFAULT 1,
    error_kind TEXT,
    latency_ms INTEGER,
    estimated_cost_inr DOUBLE PRECISION,
    created_at TEXT
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_llm_call_log_case ON llm_call_log (case_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_call_log_agent ON llm_call_log (agent)",
    "CREATE INDEX IF NOT EXISTS idx_llm_call_log_created ON llm_call_log (created_at)",
)

# The agent currently executing, so the wrapper can attribute a call without
# every call site having to thread a label through LangChain.
_current: Dict[str, Optional[str]] = {"agent": None, "case_id": None, "tenant": None}


@contextmanager
def attributed_to(agent: str, case_id: Optional[str] = None,
                  tenant_id: Optional[str] = None):
    """Attribute provider calls made inside this block to one agent."""
    previous = dict(_current)
    _current.update({"agent": agent, "case_id": case_id, "tenant": tenant_id})
    try:
        yield
    finally:
        _current.update(previous)


def _prices() -> Dict[str, Dict[str, float]]:
    """Parse the configured price list. Absent or malformed yields nothing."""
    raw = (os.getenv(_PRICE_ENV) or "").strip()
    if not raw:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    try:
        for entry in raw.split(";"):
            if ":" not in entry:
                continue
            model, spec = entry.split(":", 1)
            rates: Dict[str, float] = {}
            for part in spec.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    rates[k.strip().lower()] = float(v)
            if rates:
                out[model.strip()] = rates
    except (ValueError, TypeError):
        return {}
    return out


def estimate_cost(model: Optional[str], prompt_tokens: Optional[int],
                  completion_tokens: Optional[int]) -> Optional[float]:
    """Cost in rupees, or None when no price is configured for this model."""
    if not model or prompt_tokens is None or completion_tokens is None:
        return None
    rates = _prices().get(model)
    if not rates:
        return None
    per_m = 1_000_000
    return round(
        (prompt_tokens / per_m) * rates.get("input", 0.0)
        + (completion_tokens / per_m) * rates.get("output", 0.0),
        6,
    )


def extract_usage(payload: Any) -> Dict[str, Optional[int]]:
    """Pull token counts from a provider response, without guessing.

    Handles the OpenAI-shaped ``usage`` block that Sarvam and Groq both return,
    and LangChain's ``usage_metadata``. A response carrying no usage block gives
    Nones, which are stored as NULL - an unmeasured call is recorded as
    unmeasured, never as zero.
    """
    usage: Any = None
    if isinstance(payload, dict):
        usage = payload.get("usage")
    else:
        usage = (getattr(payload, "usage_metadata", None)
                 or (getattr(payload, "response_metadata", {}) or {}).get("token_usage"))

    if not isinstance(usage, dict):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}

    def _int(*keys):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return None

    prompt = _int("prompt_tokens", "input_tokens")
    completion = _int("completion_tokens", "output_tokens")
    total = _int("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total}


def record(agent: Optional[str] = None, *, provider: Optional[str] = None,
           model: Optional[str] = None, usage: Optional[Dict[str, Any]] = None,
           finish_reason: Optional[str] = None, succeeded: bool = True,
           error_kind: Optional[str] = None, latency_ms: Optional[int] = None,
           reasoning_chars: Optional[int] = None,
           case_id: Optional[str] = None,
           institution_id: Optional[str] = None) -> None:
    """Write one accounting row. Never raises.

    A failed call is recorded too, and deliberately so: the money that
    disappeared into calls which returned nothing is exactly what nobody could
    see before.
    """
    try:
        usage = usage or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        from app.database.database import get_app_connection

        conn = get_app_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO llm_call_log
                   (id, case_id, institution_id, agent, provider, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    reasoning_chars, finish_reason, succeeded, error_kind,
                    latency_ms, estimated_cost_inr, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    case_id or _current.get("case_id"),
                    institution_id or _current.get("tenant"),
                    agent or _current.get("agent") or "unattributed",
                    provider, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    reasoning_chars, finish_reason,
                    1 if succeeded else 0, error_kind, latency_ms,
                    estimate_cost(model, prompt_tokens, completion_tokens),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Bookkeeping must never fail an appraisal.
        print(f"[METERING] could not record call: {type(exc).__name__}")


class Timer:
    """Millisecond timer for one provider call."""

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.perf_counter() - self._t0) * 1000)
        return False
