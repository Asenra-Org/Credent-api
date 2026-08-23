"""P0-1 regression tests: borrower data must never reach application logs.

The pipeline previously printed complete LLM payloads - company name, revenue,
borrowings, net worth and credit bureau ratings - to stdout on every appraisal.
These tests fail if that behaviour returns.
"""

import asyncio
import io
import re
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from app.core.log_safety import (
    REDACTED,
    assert_no_sensitive,
    redact,
    safe_context,
    safe_error,
    scrub_secrets,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Values that stand in for a real borrower file.
BORROWER_VALUES = [
    "Anantara Agro Foods Pvt Ltd",
    "425000000",
    "182000000",
    "117500000",
    "CMR-4",
]


# ---------------------------------------------------------------------------
# redaction helpers
# ---------------------------------------------------------------------------

def test_redact_masks_financial_fields():
    payload = {
        "company_name": "Anantara Agro Foods Pvt Ltd",
        "total_revenue": 425000000,
        "total_debt": 182000000,
        "shareholder_equity": 117500000,
        "sector": "Agriculture",
        "page_count": 3,
    }
    out = redact(payload)
    assert out["company_name"] == REDACTED
    assert out["total_revenue"] == REDACTED
    assert out["total_debt"] == REDACTED
    assert out["shareholder_equity"] == REDACTED
    # Non-sensitive operational fields survive so logs stay useful.
    assert out["sector"] == "Agriculture"
    assert out["page_count"] == 3


def test_redact_handles_nested_and_list_payloads():
    payload = {"cases": [{"borrower_name": "X Ltd", "revenue": 1}], "count": 1}
    out = redact(payload)
    assert out["cases"][0]["borrower_name"] == REDACTED
    assert out["cases"][0]["revenue"] == REDACTED
    assert out["count"] == 1


def test_scrub_secrets_removes_credentials():
    text = (
        "call failed with key gsk_abcdefghijklmnopqrstuvwxyz012345 and "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    )
    out = scrub_secrets(text)
    assert "gsk_abcdefghijklmnopqrstuvwxyz012345" not in out
    assert "abcdefghijklmnopqrstuvwxyz" not in out.replace(REDACTED, "")


def test_safe_error_truncates_and_scrubs():
    exc = RuntimeError("boom gsk_abcdefghijklmnopqrstuvwxyz012345 " + "x" * 500)
    out = safe_error(exc)
    assert "gsk_" not in out
    assert len(out) <= 200


def test_safe_context_drops_unapproved_fields():
    out = safe_context(
        case_id="CASE-123",
        agent="financial_health",
        status="success",
        revenue=425000000,             # not allow-listed
        company_name="Anantara Agro",  # not allow-listed
    )
    assert "case_id=CASE-123" in out
    assert "agent=financial_health" in out
    assert "425000000" not in out
    assert "Anantara" not in out


# ---------------------------------------------------------------------------
# static guard: the removed log statements must not come back
# ---------------------------------------------------------------------------

FORBIDDEN_SOURCE_PATTERNS = [
    "SARVAM RAW INGESTION RESPONSE",
    "SARVAM RAW RESPONSE",
    "[NORMALIZE] Input:",
    "borrower_name']}",
]


@pytest.mark.parametrize("needle", FORBIDDEN_SOURCE_PATTERNS)
def test_sensitive_log_statements_absent_from_source(needle):
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if needle in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(APP_DIR)))
    assert not offenders, f"sensitive log statement {needle!r} reintroduced in {offenders}"


def test_no_raw_response_interpolated_into_logs():
    """print/logger calls must not interpolate a whole LLM response body."""
    bad = re.compile(r"(print|logger\.\w+)\([^)]*\{\s*(raw_response|res\.content|response\.content)\s*\}")
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if bad.search(text):
            offenders.append(str(path.relative_to(APP_DIR)))
    assert not offenders, f"raw LLM response logged in {offenders}"


# ---------------------------------------------------------------------------
# behavioural: run the real normaliser and assert figures do not surface
# ---------------------------------------------------------------------------

def test_normalize_does_not_print_financial_values():
    from app.agents.input.document_ingestion import normalize_to_inr

    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        normalize_to_inr("42,50,00,000")
        normalize_to_inr(425000000)
        normalize_to_inr("62 Cr")
    output = buf.getvalue()
    leaked = assert_no_sensitive(output, ["42,50,00,000", "425000000", "620000000"])
    assert not leaked, f"financial figures leaked to stdout: {leaked}"


def test_parse_failure_path_does_not_log_document_text(monkeypatch):
    """A failed extraction must not echo the borrower document into logs."""
    from app.agents.input import document_ingestion as di

    agent = di.DocumentIngestionAgent.__new__(di.DocumentIngestionAgent)
    agent.structured_llm = None

    class BoomLLM:
        def __or__(self, other):
            return self

        def __ror__(self, other):
            return self

        async def ainvoke(self, *a, **k):
            raise RuntimeError("provider unavailable")

    agent.llm = BoomLLM()

    document = (
        "Anantara Agro Foods Pvt Ltd\n"
        "Revenue from Operations: Rs. 42,50,00,000\n"
        "Total Borrowings: Rs. 18,20,00,000\n"
        "CIBIL Commercial CMR: CMR-4\n"
    )

    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        asyncio.run(agent.parse_financial_statement(document))
    output = buf.getvalue()

    leaked = assert_no_sensitive(
        output,
        ["Anantara Agro Foods", "42,50,00,000", "18,20,00,000", "CMR-4"],
    )
    assert not leaked, f"borrower document leaked to stdout: {leaked}"
