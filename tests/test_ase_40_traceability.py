"""ASE-40 evidence traceability, migrated to the CAMDocument schema.

Citation traceability is unchanged and still covered. The MetricWithCitation /
CreditAppraisalMemo models this module previously imported were replaced by the
institutional CAMDocument schema, so those assertions now target FiveCItem,
FiveCs and the evidence register.

Fallback expectations changed under P0-4: an LLM failure must not present as
MANUAL REVIEW. See test_cam_generator.py for the full gating assertions.
"""

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch

from app.agents.orchestration.cam_generator import (
    CAMDocument,
    CAMGeneratorAgent,
    Citation,
    EvidenceItem,
    FiveCItem,
    FiveCs,
)
from app.core.execution_state import AgentStatus
from app.core.output_validation import validate_cam


# ---------------------------------------------------------
# UNIT TESTS: PYDANTIC MODELS
# ---------------------------------------------------------

def test_citation_model_valid():
    """Verify Citation model serializes valid data."""
    citation = Citation(id=1, snippet="Repayment capacity is strong", page=12)
    assert citation.id == 1
    assert citation.snippet == "Repayment capacity is strong"
    assert citation.page == 12


def test_citation_model_optional_fields():
    """Verify Citation model allows optional snippet and page."""
    citation = Citation(id=1)
    assert citation.id == 1
    assert citation.snippet is None
    assert citation.page is None


def test_five_c_item_carries_evidence_and_assessment():
    """The 5C unit records the figures reasoned from, not just a verdict."""
    item = FiveCItem(
        evidence="Revenue 42,000,000 vs Debt 18,200,000",
        assessment="Adequate capacity to service debt",
        risk_implication="Low repayment risk",
    )
    assert "42,000,000" in item.evidence
    assert item.assessment.startswith("Adequate")


def test_five_c_item_defaults_are_explicit_not_empty():
    """An unpopulated dimension says NOT PROVIDED rather than silently blank."""
    item = FiveCItem()
    assert item.evidence == "NOT PROVIDED"
    assert item.assessment == "NOT PROVIDED"


def test_five_cs_covers_all_dimensions():
    """Verify FiveCs exposes all five credit dimensions."""
    item = FiveCItem(evidence="e", assessment="Good", risk_implication="Low")
    five_cs = FiveCs(
        character=item, capacity=item, capital=item,
        collateral=item, conditions=item,
    )
    for dimension in ("character", "capacity", "capital", "collateral", "conditions"):
        assert getattr(five_cs, dimension).assessment == "Good"


def test_five_cs_rejects_wrong_shape():
    """A bare string is not a 5C assessment and must not validate."""
    with pytest.raises(ValidationError):
        FiveCs(character="Good")


def test_evidence_item_traceability_fields():
    """Every evidence row must be able to name its source document and page."""
    item = EvidenceItem(
        finding="Total Revenue",
        value="425000000",
        source_document="GSTR-3B",
        page="1",
        status="VERIFIED",
    )
    assert item.source_document == "GSTR-3B"
    assert item.page == "1"
    assert item.status == "VERIFIED"


def test_evidence_item_tolerates_partial_rows():
    """P0-4B: a missing status must not invalidate the whole evidence register."""
    item = EvidenceItem(finding="Total Debt")
    assert item.status == "UNVERIFIED"
    assert item.value == "NOT PROVIDED"


def test_cam_document_preserves_evidence_register():
    doc = CAMDocument(**{
        "evidence_register": [
            {"finding": "Revenue", "value": "425000000", "source_document": "GSTR-3B",
             "page": "1", "status": "VERIFIED"},
            {"finding": "Debt", "value": "182000000"},
        ],
    })
    assert len(doc.evidence_register) == 2
    assert doc.evidence_register[0].status == "VERIFIED"
    assert doc.evidence_register[1].status == "UNVERIFIED"


# ---------------------------------------------------------
# INTEGRATION TESTS: LLM FALLBACK BEHAVIOR
# ---------------------------------------------------------

@pytest.fixture
def cam_agent():
    with patch.dict("os.environ", {"GROQ_API_KEY": "dummy_key"}):
        return CAMGeneratorAgent()


@pytest.mark.asyncio
async def test_cam_generator_llm_exception_fallback(cam_agent):
    """The fallback must be identifiable as a failure, carrying no evidence."""
    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke",
               new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.side_effect = Exception("Simulated LLM Timeout")
        result = await cam_agent.generate_cam(
            extracted_pdf_data={}, integrity_flags={}, web_research={}, final_score=85,
        )

    assert result["document_control"]["status"] == "ERROR"
    for key in ("character", "capacity", "capital", "collateral", "conditions"):
        assert key in result["five_cs"]

    # P0-4: detected as a failure rather than accepted as a MANUAL REVIEW verdict.
    status, _, reason = validate_cam(result)
    assert status is AgentStatus.FAILED, reason


@pytest.mark.asyncio
async def test_cam_generator_invalid_json_fallback(cam_agent):
    """Malformed LLM output must fail closed rather than invent a memo."""

    class MockContent:
        content = "This is a hallucinated response with no JSON whatsoever."
        response_metadata: dict = {}

    cam_agent.structured_llm = None

    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke",
               new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = MockContent()
        result = await cam_agent.generate_cam(
            extracted_pdf_data={}, integrity_flags={}, web_research={}, final_score=50,
        )

    status, _, reason = validate_cam(result)
    assert status is AgentStatus.FAILED, reason
