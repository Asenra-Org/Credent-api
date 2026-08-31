import os
import asyncio
import random
import time
from typing import Any, List, Optional

from langchain_groq import ChatGroq

# ---------------------------------------------------------------------------
# Resilient Groq client
# ---------------------------------------------------------------------------
# Groq enforces both a tokens-per-minute (TPM) and a tokens-per-day (TPD) quota
# per model. When either is hit the SDK raises and, because every agent wraps its
# LLM call in a broad try/except that returns placeholder values, the whole
# appraisal silently degrades to "Unknown Entity" / base score 65 / MANUAL REVIEW
# while still reporting HTTP 200.
#
# Two distinct failures are handled here:
#   429 rate_limit_exceeded - transient. The quota window refills, so retrying
#       the same model after a short wait usually succeeds.
#   413 request_too_large   - not transient for this model. The request itself
#       exceeds the model's per-minute ceiling, so retrying it unchanged will
#       fail identically. Move to the next model immediately.
#
# FALLBACK_LLM_MODEL_1 / FALLBACK_LLM_MODEL_2 were previously declared in .env
# but never read anywhere in the codebase, so an exhausted primary model took the
# entire pipeline down instead of rolling over. They are now honoured.
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MOVE_ON_STATUS = {413}

# 402 insufficient_quota - the account is out of credits. Unlike 429 this does
# not refill on its own, and unlike 413 it is not specific to one model: every
# model on the provider will refuse identically. Retrying or rolling over just
# burns time before the same failure, so the chain is abandoned immediately and
# the exception propagates for the caller to gate on.
_PROVIDER_EXHAUSTED_STATUS = {402}

# Transport-level failures carry no HTTP status at all: a socket timeout or a
# refused connection never reaches the point where the provider can answer.
# These are the most obviously transient failures there are, so they must be
# retried on type rather than on status.
_RETRYABLE_EXCEPTIONS = (
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
)


def _is_retryable(exc: Exception, status: Optional[int]) -> bool:
    if status is not None:
        return status in _RETRYABLE_STATUS
    return isinstance(exc, _RETRYABLE_EXCEPTIONS)


def _status_of(exc: Exception) -> Optional[int]:
    """Best-effort HTTP status extraction across groq/httpx exception shapes."""
    for attr in ("status_code", "http_status"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    # Fall back to sniffing the message; groq embeds the code in its string form.
    text = str(exc)
    for code in (*_RETRYABLE_STATUS, *_MOVE_ON_STATUS):
        if f"Error code: {code}" in text:
            return code
    return None


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter, capped so a request never hangs long."""
    return min(2.0 ** attempt, 8.0) + random.uniform(0, 0.75)


def _model_chain() -> List[str]:
    """Primary model first, then any configured fallbacks, de-duplicated."""
    chain = [
        os.getenv("PRIMARY_LLM_MODEL", GROQ_DEFAULT_MODEL),
        os.getenv("FALLBACK_LLM_MODEL_1", ""),
        os.getenv("FALLBACK_LLM_MODEL_2", ""),
    ]
    seen, out = set(), []
    for m in chain:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


class ResilientChatGroq(ChatGroq):
    """ChatGroq that retries transient rate limits and rolls over to fallback models.

    Subclassing (rather than wrapping in a Runnable) keeps ``with_structured_output``
    and every other ChatGroq method working unchanged, so no agent needs modifying.
    """

    # Declared as pydantic fields so ChatGroq's model validation accepts them.
    fallback_models: List[str] = []
    attempts_per_model: int = 3

    def _siblings(self) -> List["ChatGroq"]:
        """One client per model in the chain, primary first. Cached per instance."""
        cached = self.__dict__.get("_sibling_cache")
        if cached is not None:
            return cached
        clients: List[ChatGroq] = [self]
        for name in self.fallback_models:
            if name and name != self.model_name:
                try:
                    clients.append(self.model_copy(update={"model_name": name}))
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"[LLM] Could not build fallback client for {name}: {exc}")
        self.__dict__["_sibling_cache"] = clients
        return clients

    # -- async path (used by every agent) -----------------------------------
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        last_exc: Optional[Exception] = None
        clients = self._siblings()

        for index, client in enumerate(clients):
            model_name = client.model_name
            for attempt in range(self.attempts_per_model):
                try:
                    result = await ChatGroq._agenerate(
                        client, messages, stop=stop, run_manager=run_manager, **kwargs
                    )
                    if index or attempt:
                        print(f"[LLM] Recovered on {model_name} (attempt {attempt + 1}).")
                    return result
                except Exception as exc:
                    last_exc = exc
                    status = _status_of(exc)
                    if status in _PROVIDER_EXHAUSTED_STATUS:
                        print(
                            f"[LLM] {model_name} returned {status} (provider quota "
                            f"exhausted). Not retryable on any model - aborting."
                        )
                        raise
                    if status in _MOVE_ON_STATUS:
                        print(f"[LLM] {model_name} returned {status} (request too large). Trying next model.")
                        break
                    if not _is_retryable(exc, status):
                        raise
                    if attempt + 1 >= self.attempts_per_model:
                        print(f"[LLM] {model_name} exhausted after {attempt + 1} attempts ({status}).")
                        break
                    delay = _backoff_seconds(attempt)
                    print(f"[LLM] {model_name} returned {status}; retrying in {delay:.1f}s.")
                    await asyncio.sleep(delay)

        assert last_exc is not None
        print(f"[LLM] All models exhausted: {[c.model_name for c in clients]}")
        raise last_exc

    # -- sync path (kept consistent for any non-async caller) ---------------
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last_exc: Optional[Exception] = None
        clients = self._siblings()

        for index, client in enumerate(clients):
            model_name = client.model_name
            for attempt in range(self.attempts_per_model):
                try:
                    result = ChatGroq._generate(
                        client, messages, stop=stop, run_manager=run_manager, **kwargs
                    )
                    if index or attempt:
                        print(f"[LLM] Recovered on {model_name} (attempt {attempt + 1}).")
                    return result
                except Exception as exc:
                    last_exc = exc
                    status = _status_of(exc)
                    if status in _PROVIDER_EXHAUSTED_STATUS:
                        print(
                            f"[LLM] {model_name} returned {status} (provider quota "
                            f"exhausted). Not retryable on any model - aborting."
                        )
                        raise
                    if status in _MOVE_ON_STATUS:
                        print(f"[LLM] {model_name} returned {status} (request too large). Trying next model.")
                        break
                    if not _is_retryable(exc, status):
                        raise
                    if attempt + 1 >= self.attempts_per_model:
                        break
                    time.sleep(_backoff_seconds(attempt))

        assert last_exc is not None
        raise last_exc


# Sarvam connection facts. Declared at module level so callers that need to
# report which provider is actually live (the platform operations console) read
# the same values this factory uses, instead of duplicating them.
SARVAM_BASE_URL = "https://api.sarvam.ai/v1"
SARVAM_MODEL = "sarvam-105b"

# Default when no PRIMARY_LLM_MODEL is configured on the Groq path. Kept here so
# it is stated once.
GROQ_DEFAULT_MODEL = "llama-3.1-8b-instant"


def active_provider() -> dict:
    """Which provider an agent constructed right now would actually use.

    This mirrors the precedence in ``ChatGroqWithFallback.__new__`` exactly:
    a configured SARVAM_API_KEY overrides the Groq path entirely, including the
    PRIMARY_LLM_MODEL / FALLBACK_LLM_MODEL_* chain and the ResilientChatGroq
    retry-and-rollover wrapper.

    Returns a description only. No key value is read or returned.
    """
    if os.getenv("SARVAM_API_KEY"):
        return {
            "provider": "sarvam",
            "endpoint": SARVAM_BASE_URL,
            "primary_model": SARVAM_MODEL,
            "fallback_models": [],
            # The Sarvam branch returns a plain ChatOpenAI. The model-rollover
            # chain in ResilientChatGroq is not applied on this path; the SDK's
            # own max_retries=3 is the only retry behaviour.
            "model_failover_active": False,
            "sdk_retries": 3,
            "max_tokens": os.getenv("LLM_MAX_TOKENS") or "4000",
            "note": (
                "SARVAM_API_KEY is set, which takes precedence over the Groq path. "
                "PRIMARY_LLM_MODEL and FALLBACK_LLM_MODEL_* are not read while it "
                "is configured, and model rollover is inactive on this path."
            ),
        }

    chain = _model_chain()
    primary = chain[0] if chain else GROQ_DEFAULT_MODEL
    return {
        "provider": "groq" if os.getenv("GROQ_API_KEY") else None,
        "endpoint": None,
        "primary_model": primary,
        "fallback_models": [m for m in chain if m != primary],
        "model_failover_active": True,
        "sdk_retries": None,
        "max_tokens": os.getenv("LLM_MAX_TOKENS") or None,
        "note": None,
    }


class ChatGroqWithFallback:
    """Factory preserved for backwards compatibility with existing agent imports."""

    def __new__(cls, *args, **kwargs):
        sarvam_api_key = os.getenv("SARVAM_API_KEY")
        if sarvam_api_key:
            # Imported lazily: langchain_openai is only needed for the optional
            # Sarvam path and is NOT a declared dependency in requirements.txt.
            # A module-level import crashes Groq-only deployments on startup.
            from langchain_openai import ChatOpenAI

            # Ensure max_tokens defaults to LLM_MAX_TOKENS from env (fallback to 4000)
            max_tokens = kwargs.get("max_tokens")
            if max_tokens is None:
                max_tokens = int(os.getenv("LLM_MAX_TOKENS", 4000))
                
            kwargs.pop("api_key", None)
            return ChatOpenAI(
                base_url=SARVAM_BASE_URL,
                api_key=sarvam_api_key,
                model=SARVAM_MODEL,
                temperature=kwargs.get("temperature", 0.1),
                max_tokens=None, # Prevents LangChain from sending max_completion_tokens
                model_kwargs={"extra_body": {"max_tokens": max_tokens}},
                timeout=None,
                max_retries=3,
            )

        chain = _model_chain()
        # Agents pass model=... explicitly; honour it as primary and append the
        # configured fallbacks after it.
        primary = kwargs.pop("model", None) or (chain[0] if chain else "openai/gpt-oss-20b")
        fallbacks = [m for m in chain if m != primary]
        return ResilientChatGroq(model=primary, fallback_models=fallbacks, *args, **kwargs)
