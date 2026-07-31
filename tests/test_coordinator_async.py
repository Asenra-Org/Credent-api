import os
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.agents.orchestration.coordinator import AgentCoordinator

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def coordinator_instance():
    """Returns a coordinator instance with properly configured mock sub-agents returning coroutines."""
    coordinator = AgentCoordinator()
    
    coordinator.ingestion_agent = MagicMock()
    coordinator.ingestion_agent.ingest_pdf = AsyncMock()
    coordinator.ingestion_agent.parse_financial_statement = AsyncMock()
    
    coordinator.financial_agent = MagicMock()
    coordinator.financial_agent.analyze = AsyncMock()
    
    coordinator.management_agent = MagicMock()
    coordinator.management_agent.analyze = AsyncMock()
    
    coordinator.sector_agent = MagicMock()
    coordinator.sector_agent.get_sector_outlook = AsyncMock()
    coordinator.sector_agent.check_rbi_policies = AsyncMock()
    
    coordinator.integrity_agent = MagicMock()
    coordinator.integrity_agent.cross_validate = AsyncMock()
    
    coordinator.cam_agent = MagicMock()
    coordinator.cam_agent.generate_cam = AsyncMock()
    
    return coordinator

# ---------------------------------------------------------------------------
# run_appraisal() Async Orchestration & Concurrency Tests
# ---------------------------------------------------------------------------

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_successful_orchestration(mock_exists, coordinator_instance):
    """Verify end-to-end execution of run_appraisal under normal parameters."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Clean parsed PDF content"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra Corp", "sector": "Steel"}

    coordinator_instance.financial_agent.analyze.return_value = {"financial_health_score": 75.0, "ratios": {}}
    coordinator_instance.management_agent.analyze.return_value = {"management_score": 0.0}
    coordinator_instance.sector_agent.get_sector_outlook.return_value = {"outlook": "Stable"}
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {"flags": []}
    coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "APPROVE"}
    coordinator_instance.generate_explanation = AsyncMock(return_value="Detailed credit audit pass.")

    payload = {"file_path": "test_statement.pdf"}
    result = await coordinator_instance.run_appraisal(payload)

    assert result["status"] == "success"
    assert "appraisal_id" in result
    assert result["combined_decision"]["decision"] == "APPROVE"

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_sequential_ingestion_fail(mock_exists, coordinator_instance):
    """Verify that if sequential ingestion fails, run_appraisal immediately halts."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"error": "OCR failed to read PDF."}

    with pytest.raises(ValueError) as exc:
        await coordinator_instance.run_appraisal({"file_path": "test_statement.pdf"})
    assert "Ingestion failed" in str(exc.value)

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_single_agent_failure(mock_exists, coordinator_instance):
    """Verify that a failure in one downstream agent does not crash the coordinator."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Text content"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra Corp", "sector": "Steel"}

    coordinator_instance.financial_agent.analyze.return_value = {"financial_health_score": 70.0, "ratios": {}}
    coordinator_instance.management_agent.analyze.side_effect = RuntimeError("Management API Down")
    coordinator_instance.sector_agent.get_sector_outlook.return_value = {}
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {}
    coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "APPROVE"}
    coordinator_instance.generate_explanation = AsyncMock(return_value="Explanation summary.")

    result = await coordinator_instance.run_appraisal({"file_path": "test_statement.pdf"})
    assert result["status"] == "success"
    
    mgt_output = result["individual_agent_outputs"]["management_quality"]
    assert mgt_output["management_score"] == 0.0
    assert mgt_output["risk_level"] == "Undetermined"

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_multiple_agent_failures(mock_exists, coordinator_instance):
    """Verify multiple downstream agent failures map to their fallback states."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Text content"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra Corp", "sector": "Steel"}

    coordinator_instance.financial_agent.analyze.side_effect = RuntimeError("DB timeout")
    coordinator_instance.management_agent.analyze.return_value = {"management_score": 10.0}
    coordinator_instance.sector_agent.get_sector_outlook.side_effect = RuntimeError("Auth failure")
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {}
    coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "REJECT"}
    coordinator_instance.generate_explanation = AsyncMock(return_value="Explanation.")

    result = await coordinator_instance.run_appraisal({"file_path": "test_statement.pdf"})
    assert result["status"] == "success"
    
    fin_out = result["individual_agent_outputs"]["financial_health"]
    assert fin_out["financial_health_score"] == 50.0
    assert "Financial analysis defaulted due to agent failure." in fin_out["analysis_notes"]

def mock_wait_for_with_cleanup(return_value=None, side_effect=None):
    """Custom mock for asyncio.wait_for that properly closes/cancels unawaited coroutines to prevent resource leaks."""
    async def _mock_wait_for(fut, timeout=None):
        try:
            if side_effect:
                if isinstance(side_effect, Exception):
                    raise side_effect
                elif callable(side_effect):
                    res = side_effect()
                    if asyncio.iscoroutine(res):
                        await res
                    return res
                else:
                    raise side_effect
            return return_value
        finally:
            if asyncio.iscoroutine(fut):
                fut.close()
            elif isinstance(fut, asyncio.Future):
                fut.cancel()

    return _mock_wait_for

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_timeout_handling(mock_exists, coordinator_instance):
    """Verify that timeout during gather executes all fallbacks."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Text content"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra Corp", "sector": "Steel"}

    # Patch wait_for to throw timeout cleanly
    with patch("app.agents.orchestration.coordinator.asyncio.wait_for", side_effect=mock_wait_for_with_cleanup(side_effect=asyncio.TimeoutError())):
        coordinator_instance.generate_explanation = AsyncMock(return_value="Rationale fallback.")
        coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "PENDING"}

        result = await coordinator_instance.run_appraisal({"file_path": "test_statement.pdf"})
        assert result["status"] == "success"
        assert result["individual_agent_outputs"]["financial_health"]["financial_health_score"] == 50.0

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_evidence_generation_failure(mock_exists, coordinator_instance):
    """Verify that evidence aggregation failure degrades gracefully to empty trail."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Text content long enough"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra Corp"}

    coordinator_instance.financial_agent.analyze.return_value = {"financial_health_score": 70.0}
    coordinator_instance.management_agent.analyze.return_value = {}
    coordinator_instance.sector_agent.get_sector_outlook.return_value = {}
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {}
    coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "APPROVE"}

    coordinator_instance.build_evidence_trail = AsyncMock(side_effect=TypeError("Null reference pointer"))
    coordinator_instance.generate_explanation = AsyncMock(return_value="Decayed trail summary")

    result = await coordinator_instance.run_appraisal({"file_path": "test_statement.pdf"})
    assert result["status"] == "success"
    
    trail = result["evidence_trail"]
    assert len(trail) == 1
    assert trail[0]["title"] == "Evidence Trail Degraded"

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_explanation_generation_failure(mock_exists, coordinator_instance):
    """Verify explanation generation failures are caught cleanly."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Clean text"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra Corp"}

    coordinator_instance.financial_agent.analyze.return_value = {"financial_health_score": 80.0}
    coordinator_instance.management_agent.analyze.return_value = {}
    coordinator_instance.sector_agent.get_sector_outlook.return_value = {}
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {}
    coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "APPROVE"}

    coordinator_instance.generate_explanation = AsyncMock(side_effect=RuntimeError("Groq Service Error"))

    with pytest.raises(RuntimeError):
        await coordinator_instance.run_appraisal({"file_path": "test_statement.pdf"})

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_cancellation(mock_exists, coordinator_instance):
    """Verify that canceling a run cancels downstream tasks cleanly."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Document text"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra"}

    async def slow_analyze(*args, **kwargs):
        await asyncio.sleep(10)
        return {}

    coordinator_instance.financial_agent.analyze.side_effect = slow_analyze

    task = asyncio.create_task(coordinator_instance.run_appraisal({"file_path": "statement.pdf"}))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

@patch("app.agents.orchestration.coordinator.os.path.exists", return_value=True)
async def test_run_appraisal_return_exceptions_gather_verification(mock_exists, coordinator_instance):
    """Verify that asyncio.gather is called with return_exceptions=True."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {"text": "Clean PDF text"}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {"company_name": "Asenra"}

    coordinator_instance.financial_agent.analyze.side_effect = Exception("Task A Fail")
    coordinator_instance.management_agent.analyze.return_value = {"status": "success"}
    coordinator_instance.sector_agent.get_sector_outlook.return_value = {"status": "success"}
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {"status": "success"}
    coordinator_instance.cam_agent.generate_cam.return_value = {"decision": "APPROVE"}
    coordinator_instance.generate_explanation = AsyncMock(return_value="Audit pass.")

    with patch("app.agents.orchestration.coordinator.asyncio.gather", wraps=asyncio.gather) as mock_gather:
        await coordinator_instance.run_appraisal({"file_path": "statement.pdf"})
        called_args, called_kwargs = mock_gather.call_args
        assert called_kwargs.get("return_exceptions") is True

@pytest.mark.parametrize('score, exception_type', [
    (50, RuntimeError('LLM Timeout')),
    (45, ValueError('JSON parsing failure')),
    (90, Exception('API timeout'))
])
@patch('app.agents.orchestration.coordinator.os.path.exists', return_value=True)
async def test_run_appraisal_cam_generation_infrastructure_failures(mock_exists, coordinator_instance, score, exception_type):
    """Verify that any infrastructure failure during CAM generation results in a strict MANUAL REVIEW fallback with withheld recommendations, regardless of the score."""
    coordinator_instance.ingestion_agent.ingest_pdf.return_value = {'text': 'Valid text'}
    coordinator_instance.ingestion_agent.parse_financial_statement.return_value = {'company_name': 'Corp'}
    coordinator_instance.financial_agent.analyze.return_value = {'financial_health_score': score}
    coordinator_instance.management_agent.analyze.return_value = {}
    coordinator_instance.sector_agent.get_sector_outlook.return_value = {}
    coordinator_instance.sector_agent.check_rbi_policies.return_value = []
    coordinator_instance.integrity_agent.cross_validate.return_value = {}
    
    # Simulate infrastructure failure
    coordinator_instance.cam_agent.generate_cam.side_effect = exception_type
    coordinator_instance.generate_explanation = AsyncMock(return_value='Rationale')

    result = await coordinator_instance.run_appraisal({'file_path': 'test_statement.pdf'})
    
    assert result['status'] == 'success'
    combined = result['combined_decision']
    assert combined['decision'] == 'MANUAL REVIEW'
    assert combined['recommended_loan_amount'] == 'Withheld'
    assert combined['recommended_interest_rate'] == 'Withheld'
    assert 'Underwriting could not be completed' in combined['decision_rationale']
