"""P0-3 - deterministic configuration for the credit decision path.

Every LLM call in CRESEM is classified as either DECISION_PATH or
NON_DECISION_CONTENT.

A call is DECISION_PATH when its output can change:
  * an extracted financial figure,
  * a risk score,
  * a recommendation, decision status or credit limit,
  * material CAM content a credit officer relies on (including the rationale a
    borrower or regulator may later be shown).

Every DECISION_PATH call runs at ``temperature=0``.

Important caveat, deliberately not overstated: ``temperature=0`` makes sampling
greedy, it does **not** guarantee bit-identical output. Groq (like every hosted
provider) gives no determinism guarantee - batching, kernel selection and model
updates on their side can all change a result. Determinism here is therefore
*best effort*; reproducibility is achieved by recording provenance
(``app.core.provenance``) so any past decision can be attributed to the exact
model, prompt version and temperature that produced it.
"""

from __future__ import annotations

from typing import Dict

# Greedy decoding for anything that can move a credit outcome.
DECISION_PATH_TEMPERATURE: float = 0.0

# Reserved for prose that cannot change an outcome. Nothing uses it today; any
# future use must be recorded in provenance so the variance is auditable.
NON_DECISION_TEMPERATURE: float = 0.3

# Explicit classification. Tested by tests/test_p0_3_determinism.py, which fails
# if an agent is added without being classified.
DECISION_PATH_AGENTS: frozenset[str] = frozenset({
    "document_ingestion",     # extracted figures drive every downstream number
    "financial_health",       # ratios and financial score
    "risk_intelligence",      # adjusts the credit score
    "sector_context",         # sector risk feeds scoring
    "management_quality",     # management score feeds scoring
    "realtime_intelligence",  # research findings feed risk adjustment
    "cam_generator",          # produces the decision and recommendation
    "coordinator",            # decision rationale shown to officers/regulators
})

NON_DECISION_AGENTS: frozenset[str] = frozenset()


def temperature_for(agent: str) -> float:
    """Return the required temperature for an agent's LLM calls."""
    if agent in NON_DECISION_AGENTS:
        return NON_DECISION_TEMPERATURE
    # Unclassified agents default to the safe (deterministic) setting.
    return DECISION_PATH_TEMPERATURE


def is_decision_path(agent: str) -> bool:
    return agent not in NON_DECISION_AGENTS


def describe() -> Dict[str, object]:
    """Machine-readable summary, surfaced by the health endpoint if wanted."""
    return {
        "decision_path_temperature": DECISION_PATH_TEMPERATURE,
        "non_decision_temperature": NON_DECISION_TEMPERATURE,
        "decision_path_agents": sorted(DECISION_PATH_AGENTS),
        "non_decision_agents": sorted(NON_DECISION_AGENTS),
        "provider_note": (
            "langchain_groq clamps temperature=0 to 1e-8 because the Groq API "
            "rejects exactly 0; provenance therefore records 1e-08, which is "
            "the effective greedy setting, not a silently ignored value"
        ),
        "determinism_guarantee": (
            "temperature=0 is greedy decoding, not a provider determinism "
            "guarantee; reproducibility relies on recorded provenance"
        ),
    }
