import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch
from app.agents.orchestration.cam_generator import CAMGeneratorAgent, Citation, MetricWithCitation, FiveCs, CreditAppraisalMemo

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

def test_metric_with_citation_valid():
    """Verify MetricWithCitation accepts text and citations."""
    metric = MetricWithCitation(
        text="Analysis with marker [1]",
        citations=[Citation(id=1, snippet="Evidence", page=2)]
    )
    assert metric.text == "Analysis with marker [1]"
    assert len(metric.citations) == 1

def test_metric_with_citation_empty_citations():
    """Verify MetricWithCitation defaults to an empty list."""
    metric = MetricWithCitation(text="Analysis without marker")
    assert metric.text == "Analysis without marker"
    assert isinstance(metric.citations, list)
    assert len(metric.citations) == 0

def test_five_cs_schema_enforcement():
    """Verify FiveCs model requires MetricWithCitation across all 5 dimensions."""
    valid_metric = MetricWithCitation(text="Good")
    five_cs = FiveCs(
        character=valid_metric,
        capacity=valid_metric,
        capital=valid_metric,
        collateral=valid_metric,
        conditions=valid_metric
    )
    assert five_cs.character.text == "Good"
    
    with pytest.raises(ValidationError):
        # Passing a raw string instead of MetricWithCitation should fail
        FiveCs(
            character="Good",
            capacity=valid_metric,
            capital=valid_metric,
            collateral=valid_metric,
            conditions=valid_metric
        )

# ---------------------------------------------------------
# INTEGRATION TESTS: LLM FALLBACK BEHAVIOR
# ---------------------------------------------------------

@pytest.fixture
def cam_agent():
    with patch.dict("os.environ", {"GROQ_API_KEY": "dummy_key"}):
        return CAMGeneratorAgent()

@pytest.mark.asyncio
async def test_cam_generator_llm_exception_fallback(cam_agent):
    """Verify the fallback dictionary strictly adheres to the new MetricWithCitation schema."""
    
    # Force an exception during LLM invocation
    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.side_effect = Exception("Simulated LLM Timeout")
        
        result = await cam_agent.generate_cam(
            extracted_pdf_data={},
            integrity_flags={},
            web_research={},
            final_score=85
        )
        
        # Verify Fallback Structure
        assert result["decision"] == "MANUAL REVIEW"
        
        # Check all 5 keys exist and match the schema
        for key in ["character", "capacity", "capital", "collateral", "conditions"]:
            assert key in result["five_cs"]
            assert "text" in result["five_cs"][key]
            assert "citations" in result["five_cs"][key]
            assert result["five_cs"][key]["text"] == "Manual review required due to system error."
            assert result["five_cs"][key]["citations"] == []

@pytest.mark.asyncio
async def test_cam_generator_invalid_json_fallback(cam_agent):
    """Verify parsing fallback handles completely malformed LLM string outputs."""
    
    # Force the unstructured LLM to return a garbage string
    class MockContent:
        content = "This is a hallucinated response with no JSON whatsoever."
    
    cam_agent.structured_llm = None  # Force raw text extraction mode
    
    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = MockContent()
        
        result = await cam_agent.generate_cam(
            extracted_pdf_data={},
            integrity_flags={},
            web_research={},
            final_score=50
        )
        
        # ValueError("No JSON found") should have been caught, triggering fallback
        assert result["decision"] == "MANUAL REVIEW"
        assert result["five_cs"]["character"]["citations"] == []
