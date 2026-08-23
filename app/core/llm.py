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
        os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b"),
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
                    if status in _MOVE_ON_STATUS:
                        print(f"[LLM] {model_name} returned {status} (request too large). Trying next model.")
                        break
                    if status not in _RETRYABLE_STATUS:
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
                    if status in _MOVE_ON_STATUS:
                        print(f"[LLM] {model_name} returned {status} (request too large). Trying next model.")
                        break
                    if status not in _RETRYABLE_STATUS:
                        raise
                    if attempt + 1 >= self.attempts_per_model:
                        break
                    time.sleep(_backoff_seconds(attempt))

        assert last_exc is not None
        raise last_exc


class ChatGroqWithFallback:
    """Factory preserved for backwards compatibility with existing agent imports."""

    def __new__(cls, *args, **kwargs):
        sarvam_api_key = os.getenv("SARVAM_API_KEY")
        if sarvam_api_key:
            # Imported lazily: langchain_openai is only needed for the optional
            # Sarvam path and is NOT a declared dependency in requirements.txt.
            # A module-level import crashes Groq-only deployments on startup.
            from langchain_openai import ChatOpenAI

            kwargs.pop("api_key", None)
            return ChatOpenAI(
                base_url="https://api.sarvam.ai/v1",
                api_key=sarvam_api_key,
                model="sarvam-105b",
                temperature=kwargs.get("temperature", 0.1),
                max_tokens=kwargs.get("max_tokens", None),
                timeout=None,
                max_retries=0,
            )

        chain = _model_chain()
        # Agents pass model=... explicitly; honour it as primary and append the
        # configured fallbacks after it.
        primary = kwargs.pop("model", None) or (chain[0] if chain else "openai/gpt-oss-20b")
        fallbacks = [m for m in chain if m != primary]
        return ResilientChatGroq(model=primary, fallback_models=fallbacks, *args, **kwargs)
