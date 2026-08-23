"""Safe logging helpers for borrower data.

CRESEM processes borrower financial statements, credit bureau reports and bank
records. None of that may reach application logs: logs are retained longer,
replicated more widely and access-controlled more loosely than the database.

This module provides:
  * ``SENSITIVE_KEYS`` - the field names that must never be logged.
  * ``redact()``       - deep-redacts a payload for the rare case where a
                         structure genuinely needs to be inspected.
  * ``safe_context()`` - builds the approved metadata string used by agents.
  * ``safe_error()``   - renders an exception without leaking any payload the
                         provider may have echoed back into the message.

Agents log *what happened*, never *what the borrower said*.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# Field names that identify borrower financial or personal data. Matching is
# case-insensitive and substring-based, so "total_revenue" matches "revenue".
SENSITIVE_KEYS: frozenset[str] = frozenset({
    "revenue", "turnover", "debt", "borrowing", "borrowings",
    "net_worth", "shareholder_equity", "equity", "current_assets",
    "current_liabilities", "ebitda", "pat", "pbt",
    "cibil", "cmr", "credit_score", "bureau",
    "account_number", "account_no", "bank_account", "ifsc",
    "pan", "gstin", "gst_number", "aadhaar",
    "company_name", "borrower_name", "legal_name", "promoter",
    "raw_text", "raw_document_data", "document_text", "snippet",
    "qualitative_notes", "cam_report", "financial_ratios",
    "password", "password_hash", "token", "access_token",
    "refresh_token", "api_key", "secret", "authorization",
})

REDACTED = "[REDACTED]"

# Credentials that may appear inline inside an exception message.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}", re.IGNORECASE),
)

# Metadata fields that are safe to log. Anything outside this set is dropped by
# safe_context() rather than trusted to the caller's judgement.
SAFE_CONTEXT_KEYS: frozenset[str] = frozenset({
    "case_id", "appraisal_id", "organization_id", "institution_id",
    "tenant_id", "agent", "agent_name", "status", "duration",
    "duration_ms", "error_code", "request_id", "model", "provider",
    "attempt", "step", "count", "page_count", "retryable",
})


def _is_sensitive(key: str) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively replace sensitive values with ``[REDACTED]``.

    Used only where a structure must genuinely be inspected. The default
    posture remains: do not log payloads at all.
    """
    if _depth > 6:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if _is_sensitive(k) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(v, _depth + 1) for v in value]
    return value


def scrub_secrets(text: str) -> str:
    """Strip credential-shaped substrings from free text."""
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def safe_error(exc: BaseException, limit: int = 200) -> str:
    """Render an exception for logs without leaking payloads or secrets.

    Provider errors frequently echo part of the request back in the message, so
    the text is scrubbed and truncated rather than logged verbatim.
    """
    return scrub_secrets(f"{type(exc).__name__}: {exc}")[:limit]


def safe_context(**fields: Any) -> str:
    """Build a ``key=value | key=value`` string from allow-listed metadata only."""
    parts: list[str] = []
    for key, value in fields.items():
        if value is None or key not in SAFE_CONTEXT_KEYS:
            continue
        parts.append(f"{key}={value}")
    return " | ".join(parts)


def assert_no_sensitive(text: str, values: Iterable[Any]) -> list[str]:
    """Return any of ``values`` that appear in ``text``. Used by regression tests."""
    leaked: list[str] = []
    haystack = str(text)
    for value in values:
        token = str(value)
        if len(token) >= 4 and token in haystack:
            leaked.append(token)
    return leaked
