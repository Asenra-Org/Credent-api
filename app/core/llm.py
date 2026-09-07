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

# sarvam-105b is a reasoning model: it spends output tokens thinking before it
# writes anything into `content`. A CAM-sized answer needs room for both. At
# 4096 the reasoning alone consumed the entire budget and every response came
# back with content="" - the model never reached its answer. This is the floor
# for the whole pipeline; do not lower it below the cost of reasoning.
DEFAULT_MAX_TOKENS = 16384


def configured_max_tokens() -> int:
    """The output token budget, read from the environment with a safe floor."""
    try:
        value = int(os.getenv("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return value if value > 0 else DEFAULT_MAX_TOKENS

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
            "max_tokens": configured_max_tokens(),
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
        "max_tokens": configured_max_tokens(),
        "note": None,
    }


class ChatGroqWithFallback:
    """Factory preserved for backwards compatibility with existing agent imports."""

    def __new__(cls, *args, **kwargs):
        sarvam_api_key = os.getenv("SARVAM_API_KEY")
        if sarvam_api_key:
            from langchain_core.language_models.chat_models import BaseChatModel
            from langchain_core.outputs import ChatResult, ChatGeneration
            from langchain_core.messages import AIMessage
            from typing import Any, List, Optional
            import httpx

            class SarvamChatWrapper(BaseChatModel):
                api_key: str
                model: str = "sarvam-105b"
                temperature: float = 0.1
                max_tokens: int = 4000
                
                @property
                def _llm_type(self) -> str:
                    return "sarvam-chat-wrapper"

                async def _agenerate(self, messages: List[Any], stop: Optional[List[str]] = None, run_manager: Optional[Any] = None, **kwargs: Any) -> ChatResult:
                    sarvam_messages = []
                    for msg in messages:
                        # LangChain message types: HumanMessage -> human, AIMessage -> ai, SystemMessage -> system
                        msg_type = getattr(msg, "type", "human")
                        role = "user" if msg_type == "human" else "assistant" if msg_type == "ai" else msg_type
                        sarvam_messages.append({"role": role, "content": getattr(msg, "content", str(msg))})
                        
                    payload = {
                        "model": self.model,
                        "messages": sarvam_messages,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens
                    }
                    
                    headers = {
                        "Content-Type": "application/json",
                        "API-Subscription-Key": self.api_key,
                    }
                    
                    async with httpx.AsyncClient(timeout=180.0) as client:
                        resp = await client.post("https://api.sarvam.ai/v1/chat/completions", json=payload, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()
                        
                    choices = data.get("choices", [])
                    if not choices:
                        raise ValueError("No choices in Sarvam response")
                        
                    message_data = choices[0].get("message", {})
                    content = message_data.get("content", "")
                    reasoning = message_data.get("reasoning_content", "")
                    finish_reason = choices[0].get("finish_reason")

                    if finish_reason == "length":
                        # Cut off at the token ceiling. Whether `content` is
                        # empty (never stopped reasoning) or partial (stopped
                        # mid-answer), what came back is not the model's
                        # conclusion. Parsing either yields data that looks
                        # complete and is not, so both fail here and the
                        # safety gate reports the run as incomplete.
                        raise ValueError(
                            f"{self.model} was truncated at the "
                            f"{self.max_tokens}-token ceiling "
                            f"({len(content)} chars of content). Raise "
                            f"LLM_MAX_TOKENS (floor {DEFAULT_MAX_TOKENS})."
                        )

                    if not content and reasoning:
                        # The model finished but placed its answer in the
                        # reasoning channel. Recovering it is legitimate;
                        # doing so after a `length` finish is not, which is
                        # why the check above comes first.
                        import re
                        match = re.search(r'(\{[\s\S]+)', reasoning)
                        content = match.group(1) if match else reasoning
                        print(
                            f"[SARVAM WRAPPER] content empty with finish_reason="
                            f"{finish_reason!r}; recovered {len(content)} chars "
                            f"from reasoning_content"
                        )

                    if not content:
                        raise ValueError(
                            f"{self.model} returned an empty response "
                            f"(finish_reason={finish_reason!r})"
                        )
                        
                    msg_out = AIMessage(content=content)
                    gen = ChatGeneration(message=msg_out, text=content)
                    return ChatResult(generations=[gen])

                def _generate(self, messages: List[Any], stop: Optional[List[str]] = None, run_manager: Optional[Any] = None, **kwargs: Any) -> ChatResult:
                    raise NotImplementedError("Only async generation is supported")

            max_tokens = kwargs.get("max_tokens")
            if max_tokens is None:
                max_tokens = configured_max_tokens()
                
            return SarvamChatWrapper(
                api_key=sarvam_api_key,
                model=SARVAM_MODEL,
                temperature=kwargs.get("temperature", 0.1),
                max_tokens=max_tokens
            )

        chain = _model_chain()
        # Agents pass model=... explicitly; honour it as primary and append the
        # configured fallbacks after it.
        primary = kwargs.pop("model", None) or (chain[0] if chain else "openai/gpt-oss-20b")
        fallbacks = [m for m in chain if m != primary]
        return ResilientChatGroq(model=primary, fallback_models=fallbacks, *args, **kwargs)
