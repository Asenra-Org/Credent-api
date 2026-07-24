import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from app.routes.analysis import AppraisalRequest, _map_to_db_payload

# ---------------------------------------------------------------------------
# Route Endpoint Tests
# ---------------------------------------------------------------------------

def test_route_appraise_endpoint_success(client):
    """Verify POST /api/v1/analysis/appraise returns 200 and saves record."""
    mock_appraisal = {
        "status": "success",
        "appraisal_id": "APPRAISAL_999",
        "individual_agent_outputs": {
            "ingestion": {"company_name": "TestCorp", "sector": "Steel", "total_revenue": 10000000.0, "base_score": 75},
            "financial_health": {"financial_health_score": 80.0, "ratios": {}},
            "management_quality": {"management_score": 0.0},
            "sector_context": {"risk_factors": []},
            "integrity_check": {}
        },
        "combined_decision": {
            "decision": "APPROVE",
            "recommended_loan_amount": "50L",
            "recommended_interest_rate": "11.0%",
            "decision_rationale": "Healthy indicators."
        },
        "evidence_trail": [],
        "explanation": "Rationale summary details."
    }

    mock_coordinator = MagicMock()
    mock_coordinator.run_appraisal = AsyncMock(return_value=mock_appraisal)

    with patch("app.routes.analysis.coordinator", mock_coordinator), \
         patch("app.routes.analysis.save_appraisal", MagicMock(return_value="DB_REC_777")):

        payload = {
            "file_path": "valid_statement.pdf",
            "gst_data": [],
            "bank_data": [],
            "promoter_ids": []
        }
        response = client.post("/api/v1/analysis/appraise", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["record_id"] == "DB_REC_777"
        assert data["appraisal_id"] == "APPRAISAL_999"

def test_route_appraise_endpoint_validation_failure(client):
    """Verify invalid payloads (e.g. empty file_path) yield 422 errors."""
    payload = {
        "file_path": ""  # Invalid empty path
    }
    response = client.post("/api/v1/analysis/appraise", json=payload)
    assert response.status_code == 422

def test_route_appraise_endpoint_persistence_failure_bypassed(client):
    """Verify database write errors do not abort the API request."""
    mock_appraisal = {
        "status": "success",
        "appraisal_id": "APPRAISAL_999",
        "individual_agent_outputs": {
            "ingestion": {"company_name": "TestCorp"},
            "financial_health": {},
            "management_quality": {},
            "sector_context": {},
            "integrity_check": {}
        },
        "combined_decision": {"decision": "REJECT"},
        "evidence_trail": [],
        "explanation": "Fail rationale."
    }

    mock_coordinator = MagicMock()
    mock_coordinator.run_appraisal = AsyncMock(return_value=mock_appraisal)

    with patch("app.routes.analysis.coordinator", mock_coordinator), \
         patch("app.routes.analysis.save_appraisal", MagicMock(side_effect=ConnectionError("Supabase network timeout"))):

        payload = {
            "file_path": "valid_statement.pdf"
        }
        response = client.post("/api/v1/analysis/appraise", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert "record_id" not in data
        assert data["status"] == "success"

def test_adapter_map_to_db_payload_mapping():
    """Verify the private helper translates the coordinator model into the flat DB structure."""
    request = AppraisalRequest(file_path="statement.pdf")
    coordinator_output = {
        "appraisal_id": "APP_100",
        "individual_agent_outputs": {
            "ingestion": {"company_name": "Asenra", "sector": "SaaS", "total_revenue": 50000.0, "base_score": 70},
            "financial_health": {"financial_health_score": 65.0, "ratios": {"current_ratio": 1.2}},
            "management_quality": {"management_score": 10.0, "promoter_analysis": [], "governance_assessment": {}},
            "sector_context": {"risk_factors": ["Market headwind"]},
            "integrity_check": {"flags": []}
        },
        "combined_decision": {
            "decision": "APPROVE",
            "recommended_loan_amount": "10L",
            "recommended_interest_rate": "12.0%",
            "decision_rationale": "Acceptable risks."
        },
        "explanation": "Summary analysis details."
    }

    db_payload = _map_to_db_payload("APP_100", request, coordinator_output)
    
    assert db_payload["company_id"] == "APP_100"
    assert db_payload["company_name"] == "Asenra"
    assert db_payload["sector"] == "SaaS"
    assert db_payload["revenue"] == 50000.0
    assert db_payload["base_score"] == 70
    assert db_payload["adjusted_score"] == 65
    assert db_payload["decision"] == "APPROVE"
    assert db_payload["recommended_loan_amount"] == "10L"
    assert db_payload["recommended_interest_rate"] == "12.0%"
    assert db_payload["decision_rationale"] == "Acceptable risks."
    assert db_payload["financial_ratios"] == {"current_ratio": 1.2}
    assert db_payload["management_score"] == 10.0
