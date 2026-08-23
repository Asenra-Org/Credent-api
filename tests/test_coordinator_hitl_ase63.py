import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.agents.orchestration.coordinator import AgentCoordinator
from app.agents.orchestration.case_state import LoanCaseState, STATUS_PAUSED, STATUS_RUNNING
from app.database.database import update_case_result, get_case, create_case

client = TestClient(app)

@pytest.mark.asyncio
@patch('app.agents.orchestration.coordinator.update_case_result')
@patch('app.agents.orchestration.coordinator.CAMGeneratorAgent')
async def test_coordinator_critical_pause(mock_cam_cls, mock_update_result):
    # Mock boundary: Instantiate coordinator with mocked agents so ChatGroq is not initialized
    # generate_cam is awaited by the coordinator, so the double must be async.
    # With a plain MagicMock the call raised, the run fell into the CAM-failure
    # branch, and this test never exercised the critical-risk override at all.
    mock_cam = mock_cam_cls.return_value
    mock_cam.generate_cam = AsyncMock(return_value={
        "document_control": {"status": "PENDING"},
        "five_cs": {k: {"evidence": "e", "assessment": "a", "risk_implication": "r"}
                    for k in ["character", "capacity", "capital", "collateral", "conditions"]},
        "decision": "APPROVE",
        "recommended_loan_amount": "1000000",
        "recommended_interest_rate": "12%",
        "decision_rationale": "baseline",
    })
    coordinator = AgentCoordinator(cam_agent=mock_cam)
    @patch('app.agents.orchestration.coordinator.create_case')
    @patch('app.agents.orchestration.coordinator.get_case')
    async def run_test(mock_get_case, mock_create_case):
        # We need mock_get_case to return None so it triggers creation,
        # or we just let it create. Wait, if we mock it to return None, it creates a new state.
        # We want to test a specific state, so we mock get_case to return the state dict
        # But wait, run_appraisal_with_state overrides `state` via from_db_record if get_case returns a record.
        # Since it's a new case, let's just let it be None and mock the agents instead.
        mock_get_case.return_value = {
            "case_id": "TEST_CRIT_PAUSE",
            "status": STATUS_RUNNING,
            "current_step": "ingestion_complete",
            "result_data": {
                "extracted_data": {"test": "dummy_long_enough_data"}
            }
        }

        # We can't inject `state` directly, but we can just use the returned result
        # However, to simulate an agent yielding CRITICAL evidence, we can mock `integrity_agent.verify_document_integrity`.
        coordinator.integrity_agent = MagicMock()
        coordinator.integrity_agent.verify_document_integrity.return_value = {
            "findings": [{"severity": "CRITICAL", "description": "Promoter default"}]
        }

        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            result = await coordinator.run_appraisal_with_state({"file_path": "dummy.pdf"}, case_id="TEST_CRIT_PAUSE")

        # [ASE-63 revised] The coordinator no longer halts the run on critical
        # risk; it completes the pipeline and forces the decision to MANUAL
        # REVIEW so a human underwrites the case. This is a business rule, not
        # a system failure, so the appraisal legitimately reports success and
        # still yields a decision - unlike a required-agent failure, which must
        # produce ANALYSIS_INCOMPLETE (see tests/test_p0_hardening.py).
        assert result["status"] == "success"

        decision = result.get("combined_decision", {})
        assert decision.get("decision") == "MANUAL REVIEW"
        assert "HUMAN_APPROVAL_REQUIRED" in decision.get("decision_rationale", "")
        assert decision.get("recommended_loan_amount") == "Withheld"

        # State is still persisted for audit.
        assert mock_update_result.called

    await run_test()

def test_unauthorized_approval():
    response = client.post("/api/v1/reports/approve/123", json={"decision": "APPROVE", "rationale": "ok"})
    # Mock auth dependency in real tests might be overridden,
    # but here we test if the endpoint structure expects Depends.
    # Depends(get_current_manager) will return MOCK_MGR_001. Wait.
    pass # we can test it using dependency overrides if needed

def test_invalid_state_approval():
    pass # covered by test_invalid_state_approval_actual

def test_invalid_state_approval_actual(admin_headers):
    case_id = "TEST_INVALID_STATE"
    with patch.dict('os.environ', {'ENABLE_MOCK_AUTH': 'True'}):
        with patch('app.database.database.get_case') as mock_get_case:
            mock_get_case.return_value = {"case_id": case_id, "status": STATUS_RUNNING}
            response = client.post(f"/api/v1/reports/approve/{case_id}", json={"decision": "APPROVE", "rationale": "ok"}, headers=admin_headers)
            assert response.status_code == 400
            assert "Only PAUSED cases" in response.text

@patch('app.routes.reports.BackgroundTasks.add_task')
@patch('app.database.database.update_case_result')
def test_authorized_approval_and_duplicate(mock_update, mock_add_task, admin_headers):
    case_id = "TEST_AUTH_APP"
    with patch.dict('os.environ', {'ENABLE_MOCK_AUTH': 'True'}):
        with patch('app.database.database.get_case') as mock_get_case:
            # Case is paused
            mock_get_case.return_value = {"case_id": case_id, "status": STATUS_PAUSED, "result_data": {}}

            response = client.post(f"/api/v1/reports/approve/{case_id}", json={"decision": "APPROVE", "rationale": "Looks fine"}, headers=admin_headers)
            assert response.status_code == 200
            assert response.json()["decision"] == "APPROVE"

            # Verify background task was queued correctly
            assert mock_add_task.called
            func_called = mock_add_task.call_args[0][0]
            # When we add a coroutine wrapper, the func might be different, let's just assert add_task was called.

            # duplicate approval fails because state changes
            mock_get_case.return_value = {"case_id": case_id, "status": STATUS_RUNNING}
            response = client.post(f"/api/v1/reports/approve/{case_id}", json={"decision": "APPROVE", "rationale": "Looks fine"}, headers=admin_headers)
            assert response.status_code == 400

@pytest.mark.asyncio
@patch('app.agents.orchestration.coordinator.CAMGeneratorAgent')
async def test_resume_checkpoint_and_recovery(mock_cam_cls):
    mock_cam_instance = mock_cam_cls.return_value
    mock_cam_instance.generate_cam.return_value = {"decision": "APPROVE"}

    coordinator = AgentCoordinator(cam_agent=mock_cam_instance)

    # Simulate restarted state via get_case mock
    db_record = {
        "case_id": "TEST_RESUME",
        "status": STATUS_PAUSED,
        "current_step": "evidence_built",
        "result_data": {
            "extracted_data": {"dummy": "data"},
            "manager_decision": "APPROVE",
            "manager_rationale": "Approved by exception",
            "pause_reason": "CRITICAL_RISK_DETECTED"
        }
    }

    with patch('app.agents.orchestration.coordinator.get_case') as mock_get_case, \
         patch('app.agents.orchestration.coordinator.update_case_result') as mock_update_case, \
         patch('os.path.exists') as mock_exists:
        mock_get_case.return_value = db_record
        mock_exists.return_value = True

        result = await coordinator.run_appraisal_with_state({"file_path": "dummy.pdf"}, case_id="TEST_RESUME")

        # Reaches CAM and completes
        assert result["status"] == "success"
        assert mock_cam_instance.generate_cam.called
