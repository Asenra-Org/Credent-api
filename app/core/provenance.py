"""P0-2 - decision provenance for CRESEM appraisals.

CRESEM must be able to answer, months after the fact: *exactly what produced
this appraisal?* An appraisal is assembled from several agents, and those agents
may run on different models (the resilient client in ``app.core.llm`` rolls over
to a fallback model when a provider quota is exhausted). Recording one model
name for the whole appraisal would therefore be wrong.

Provenance is captured **per agent execution**: which agent ran, on which
provider and model, with which prompt version and temperature, and - where the
provider returns one - the upstream request id.

Nothing here fabricates data. If a value is genuinely unknown it stays ``None``
and is persisted as SQL NULL, which is how every historical record (written
before this module existed) will read.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Bump when a prompt's wording changes in a way that can change extracted values
# or a credit decision. Read by agents when they record provenance.
PROMPT_VERSIONS: Dict[str, str] = {
    "document_ingestion": "2026-08-23.1",
    "financial_health": "2026-08-23.1",
    "risk_intelligence": "2026-08-23.1",
    "sector_context": "2026-08-23.1",
    "management_quality": "2026-08-23.1",
    "realtime_intelligence": "2026-08-23.1",
    "cam_generator": "2026-08-23.2",
}

# Bumped when an agent's logic (not merely its prompt) changes materially.
AGENT_VERSIONS: Dict[str, str] = {
    "document_ingestion": "1.1.0",
    "financial_health": "1.0.0",
    "risk_intelligence": "1.0.0",
    "sector_context": "1.0.0",
    "management_quality": "1.0.0",
    "realtime_intelligence": "1.0.0",
    "cam_generator": "1.1.0",
}

UNKNOWN = "UNKNOWN"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class AgentProvenance:
    """Provenance for a single agent execution within one appraisal."""

    agent: str
    provider: Optional[str] = None
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    prompt_version: Optional[str] = None
    agent_version: Optional[str] = None
    temperature: Optional[float] = None
    provider_request_id: Optional[str] = None
    status: str = "COMPLETED"
    generated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def capture(
    agent: str,
    llm: Any = None,
    status: str = "COMPLETED",
    provider_request_id: Optional[str] = None,
    response: Any = None,
) -> AgentProvenance:
    """Build provenance for one agent execution by inspecting the live client.

    Reads the model actually configured on ``llm`` rather than the environment,
    so a fallback rollover is recorded truthfully. Never raises - provenance
    capture must not be able to break an appraisal.
    """
    provider = model_name = None
    temperature = None

    def _as_str(value: Any) -> Optional[str]:
        """Accept only genuine strings.

        Test doubles and partially-initialised clients expose attributes that
        are not strings; recording those would poison the rollup (and cannot be
        sorted). An unknown model is recorded as NULL, never as a stand-in.
        """
        return value if isinstance(value, str) and value.strip() else None

    try:
        if llm is not None:
            model_name = _as_str(getattr(llm, "model_name", None)) or _as_str(getattr(llm, "model", None))
            temperature = getattr(llm, "temperature", None)
            module = type(llm).__module__ or ""
            if "groq" in module.lower() or (model_name or "").startswith(("openai/", "qwen/", "groq/")):
                provider = "groq"
            elif "openai" in module.lower():
                provider = "sarvam" if os.getenv("SARVAM_API_KEY") else "openai"
    except Exception:  # pragma: no cover - provenance must never break a run
        pass

    if provider_request_id is None and response is not None:
        provider_request_id = extract_request_id(response)

    return AgentProvenance(
        agent=agent,
        provider=provider,
        model_name=model_name,
        # Groq exposes no separate model revision; the model id is the version.
        model_version=model_name,
        prompt_version=PROMPT_VERSIONS.get(agent),
        agent_version=AGENT_VERSIONS.get(agent),
        temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
        provider_request_id=provider_request_id,
        status=status,
    )


def extract_request_id(response: Any) -> Optional[str]:
    """Pull the upstream request id out of a LangChain response, if present."""
    try:
        meta = getattr(response, "response_metadata", None) or {}
        for key in ("id", "request_id", "x-request-id"):
            if meta.get(key):
                return str(meta[key])
        headers = meta.get("headers") or {}
        for key in ("x-request-id", "x-groq-request-id"):
            if headers.get(key):
                return str(headers[key])
        meta2 = getattr(response, "additional_kwargs", None) or {}
        if meta2.get("id"):
            return str(meta2["id"])
    except Exception:  # pragma: no cover
        pass
    return None


class ProvenanceLedger:
    """Collects per-agent provenance across one appraisal run."""

    def __init__(self) -> None:
        self._entries: List[AgentProvenance] = []

    def record(self, entry: AgentProvenance) -> None:
        if entry is not None:
            self._entries.append(entry)

    def record_capture(self, agent: str, llm: Any = None, **kwargs: Any) -> AgentProvenance:
        entry = capture(agent, llm=llm, **kwargs)
        self.record(entry)
        return entry

    @property
    def entries(self) -> List[AgentProvenance]:
        return list(self._entries)

    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def to_json(self) -> str:
        return json.dumps(self.to_list())

    def models_used(self) -> List[str]:
        return sorted({e.model_name for e in self._entries if e.model_name})

    def summary(self) -> Dict[str, Any]:
        """Appraisal-level rollup stored alongside the per-agent detail.

        ``model_name`` is only populated when a single model served the whole
        appraisal; when several did, it reads MULTIPLE and the per-agent list is
        the authoritative record.
        """
        def _one(values: set) -> Optional[str]:
            """Single value if unanimous, MULTIPLE if not, None if nothing recorded."""
            clean = sorted(v for v in values if v)
            if not clean:
                return None
            return clean[0] if len(clean) == 1 else "MULTIPLE"

        models = self.models_used()
        temps = sorted({e.temperature for e in self._entries if e.temperature is not None})
        request_ids = [e.provider_request_id for e in self._entries if e.provider_request_id]
        return {
            "provider": _one({e.provider for e in self._entries}),
            "model_name": _one(set(models)),
            "model_version": _one({e.model_version for e in self._entries}),
            "prompt_version": _one({e.prompt_version for e in self._entries}),
            "agent_version": _one({e.agent_version for e in self._entries}),
            # A single temperature only when every agent agreed; otherwise the
            # per-agent record is authoritative and this stays NULL.
            "temperature": temps[0] if len(temps) == 1 else None,
            "provider_request_id": request_ids[0] if len(request_ids) == 1 else None,
            "agent_count": len(self._entries),
            "models_used": models,
            "generated_at": _utc_now(),
        }
