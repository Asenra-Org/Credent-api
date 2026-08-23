"""CAM generator tests, migrated to the CAMDocument schema.

The previous CreditAppraisalMemo / MetricWithCitation / FiveCs(text, citations)
models were replaced by the institutional CAMDocument schema, so these tests were
failing at import and blocking CI. They now exercise the current schema.

The fallback assertions have also changed deliberately. The old tests asserted
that an LLM failure produced ``decision == "MANUAL REVIEW"``. Under P0-4 that is
exactly the behaviour we forbid: a system failure must not be presentable as a
human underwriting conclusion. These tests now assert that the fallback is
*detected as invalid* and cannot yield a valid credit decision.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.agents.orchestration.cam_generator import (
    CAMDocument,
    CAMGeneratorAgent,
    Citation,
    FiveCItem,
    FiveCs,
    Recommendation,
)
from app.core.execution_state import (
    DECISION_ANALYSIS_INCOMPLETE,
    AgentResult,
    AgentStatus,
    AppraisalExecution,
    gate_decision,
)
from app.core.output_validation import validate_cam


@pytest.fixture
def cam_agent():
    with patch.dict("os.environ", {"GROQ_API_KEY": "dummy_key"}):
        return CAMGeneratorAgent()


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------

def test_prompt_contains_decision_directives(cam_agent):
    """The scoring and gap directives must survive prompt edits."""
    system_prompt = cam_agent._build_prompt().messages[0].prompt.template
    assert "CRITICAL DIRECTIVES:" in system_prompt
    assert "If the Composite Risk Score < 60, the decision MUST be REJECT." in system_prompt
    assert "severe missing gaps" in system_prompt


def test_prompt_requires_five_cs_analysis(cam_agent):
    """Regression: the 5Cs directive is what stops five_cs coming back empty."""
    system_prompt = cam_agent._build_prompt().messages[0].prompt.template
    assert "THE FIVE Cs ARE ANALYSIS, NOT EXTRACTION" in system_prompt
    for dimension in ("character", "capacity", "capital", "collateral", "conditions"):
        assert dimension in system_prompt


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_citation_with_page():
    citation = Citation(id=1, snippet="Repayment capacity is strong", page=5)
    assert citation.id == 1
    assert citation.page == 5


def test_citation_without_page():
    citation = Citation(id=2, snippet="No page marker")
    assert citation.page is None
    assert citation.document is None


def test_citation_defaults_do_not_require_id():
    """P0-4B: leaf models default so one missing field cannot fail a whole CAM."""
    assert Citation().id == 0


def test_five_c_item_defaults_to_not_provided():
    item = FiveCItem()
    assert item.evidence == "NOT PROVIDED"
    assert item.assessment == "NOT PROVIDED"
    assert item.risk_implication == "NOT PROVIDED"


def test_five_cs_accepts_all_dimensions():
    populated = FiveCItem(evidence="Rev 42m vs debt 18m", assessment="Adequate", risk_implication="Low")
    five_cs = FiveCs(
        character=populated, capacity=populated, capital=populated,
        collateral=populated, conditions=populated,
    )
    assert five_cs.capacity.assessment == "Adequate"


def test_cam_document_builds_with_no_input():
    """Every field defaults, so a partial LLM response degrades field by field."""
    doc = CAMDocument()
    assert doc.recommendation.decision == "MANUAL REVIEW"
    assert doc.five_cs.character.assessment == "NOT PROVIDED"
    assert doc.evidence_register == []


def test_cam_document_accepts_partial_evidence_register():
    """Regression for evidence_register.N.status: a missing subfield must not fail."""
    doc = CAMDocument(**{
        "evidence_register": [{"finding": "Revenue"}],       # status omitted
        "recommendation": {"decision": "APPROVE", "rationale": "ok"},
    })
    assert doc.evidence_register[0].status == "UNVERIFIED"
    assert doc.recommendation.decision == "APPROVE"


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_cam_pipeline_success(cam_agent):
    populated = FiveCItem(evidence="Score 59", assessment="Weak", risk_implication="High")
    mock_cam = CAMDocument(
        five_cs=FiveCs(
            character=populated, capacity=populated, capital=populated,
            collateral=populated, conditions=populated,
        ),
        recommendation=Recommendation(decision="REJECT", rationale="Score under 60."),
    )

    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke",
               new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = mock_cam
        result = await cam_agent.generate_cam(
            extracted_pdf_data={"test": "data"},
            integrity_flags={},
            web_research={},
            final_score=59,
        )

    assert result["decision"] == "REJECT"
    assert result["decision_rationale"] == "Score under 60."
    mock_ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_llm_exception_fallback_is_not_a_credit_decision(cam_agent):
    """P0-4: an LLM failure must not surface as MANUAL REVIEW."""
    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke",
               new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.side_effect = Exception("Simulated LLM Timeout")
        result = await cam_agent.generate_cam(
            extracted_pdf_data={}, integrity_flags={}, web_research={}, final_score=85,
        )

    # The agent still emits its error payload...
    assert result["document_control"]["status"] == "ERROR"

    # ...but validation must classify it as a failure, not a result.
    status, _, reason = validate_cam(result)
    assert status is AgentStatus.FAILED, reason

    # And gating must refuse to present it as an underwriting conclusion.
    execution = AppraisalExecution()
    execution.record(AgentResult(agent="cam_generator", status=status))
    gated = gate_decision(execution, result.get("decision"))
    assert gated["decision"] == DECISION_ANALYSIS_INCOMPLETE
    assert gated["decision"] != "MANUAL REVIEW"
    assert gated["decision_allowed"] is False


@pytest.mark.asyncio
async def test_invalid_json_fallback_is_not_a_credit_decision(cam_agent):
    """A hallucinated non-JSON response must fail closed, not default to a decision."""

    class MockContent:
        content = "This is a hallucinated response with no JSON whatsoever."
        response_metadata: dict = {}

    cam_agent.structured_llm = None  # force the raw-text extraction path

    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke",
               new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = MockContent()
        result = await cam_agent.generate_cam(
            extracted_pdf_data={}, integrity_flags={}, web_research={}, final_score=50,
        )

    status, _, reason = validate_cam(result)
    assert status is AgentStatus.FAILED, reason
