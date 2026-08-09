import pytest
from unittest.mock import AsyncMock, patch
from app.agents.orchestration.cam_generator import CAMGeneratorAgent, CreditAppraisalMemo, FiveCs, MetricWithCitation

@pytest.fixture
def cam_agent():
    with patch.dict("os.environ", {"GROQ_API_KEY": "dummy_key"}):
        return CAMGeneratorAgent()

def test_prompt_contains_strict_threshold(cam_agent):
    """Verify that the generated prompt contains the updated strict rejection logic."""
    prompt_template = cam_agent._build_prompt()
    system_prompt = prompt_template.messages[0].prompt.template
    
    # Assert the new Decision Priority is present
    assert "Decision Priority (Highest to Lowest):" in system_prompt
    assert "1. If Score < 60 -> MUST REJECT. No exceptions." in system_prompt
    assert "2. Else if financials are missing -> MANUAL REVIEW." in system_prompt
    assert "3. Else if Current Ratio < 1.0 -> MANUAL REVIEW." in system_prompt
    assert "4. Else evaluate the remaining Five Cs criteria to determine APPROVE or MANUAL REVIEW" in system_prompt

@pytest.mark.asyncio
async def test_generate_cam_pipeline_success(cam_agent):
    """Validate that the pipeline processes inputs correctly (mocking the LLM)."""
    
    metric_mock = MetricWithCitation(text="Good", citations=[])
    
    mock_cam = CreditAppraisalMemo(
        five_cs=FiveCs(
            character=metric_mock,
            capacity=metric_mock,
            capital=metric_mock,
            collateral=metric_mock,
            conditions=metric_mock
        ),
        decision="REJECT",
        recommended_loan_amount="0",
        recommended_interest_rate="N/A",
        decision_rationale="Rejected due to score under 60."
    )
    
    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = mock_cam
        
        result = await cam_agent.generate_cam(
            extracted_pdf_data={"test": "data"},
            integrity_flags={},
            web_research={},
            final_score=59
        )
        
        assert result["decision"] == "REJECT"
        assert result["recommended_loan_amount"] == "0"
        mock_ainvoke.assert_called_once()

def test_citation_with_page():
    from app.agents.orchestration.cam_generator import Citation
    citation_data = {
        "id": 1,
        "snippet": "Revenue was high",
        "page": 1
    }
    citation = Citation(**citation_data)
    assert citation.page == 1
    assert citation.id == 1

def test_citation_without_page():
    from app.agents.orchestration.cam_generator import Citation
    citation_data = {
        "id": 2,
        "snippet": "Profits were steady"
    }
    citation = Citation(**citation_data)
    assert citation.page is None
    assert citation.id == 2

def test_multiple_citations_mixed_page_availability():
    from app.agents.orchestration.cam_generator import MetricWithCitation
    payload = {
        "text": "The company shows stable growth.",
        "citations": [
            {"id": 1, "snippet": "growth is stable", "page": 5},
            {"id": 2, "snippet": "stable trend"}
        ]
    }
    metric = MetricWithCitation(**payload)
    assert len(metric.citations) == 2
    assert metric.citations[0].page == 5
    assert metric.citations[1].page is None

def test_no_validation_error_for_absent_page():
    from app.agents.orchestration.cam_generator import CreditAppraisalMemo
    payload = {
        "five_cs": {
            "character": {"text": "Good", "citations": [{"id": 1, "snippet": "no page"}]},
            "capacity": {"text": "Good", "citations": []},
            "capital": {"text": "Good", "citations": []},
            "collateral": {"text": "Good", "citations": []},
            "conditions": {"text": "Good", "citations": []}
        },
        "decision": "APPROVE",
        "recommended_loan_amount": "0",
        "recommended_interest_rate": "N/A",
        "decision_rationale": "Valid memo."
    }
    # This should not raise any ValidationError
    memo = CreditAppraisalMemo(**payload)
    assert memo.five_cs.character.citations[0].page is None
