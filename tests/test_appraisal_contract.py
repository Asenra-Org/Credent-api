import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ---------------------------------------------------------------------------
# Contract / Schema Validation Tests
# ---------------------------------------------------------------------------

def test_response_payload_contract_keys_and_types(client):
    """Verify endpoint response returns exactly the required keys and types, with no unexpected keys."""
    mock_appraisal = {
        "status": "success",
        "appraisal_id": "APPRAISAL_123",
        "individual_agent_outputs": {
            "ingestion": {},
            "financial_health": {},
            "management_quality": {},
            "sector_context": {},
            "integrity_check": {}
        },
        "combined_decision": {
            "decision": "APPROVE"
        },
        "evidence_trail": [],
        "explanation": "Rationale text."
    }

    mock_coordinator = MagicMock()
    mock_coordinator.run_appraisal = AsyncMock(return_value=mock_appraisal)

    with patch("app.routes.analysis.coordinator", mock_coordinator), \
         patch("app.routes.analysis.save_appraisal", MagicMock(return_value="REC_123")):

        payload = {"file_path": "statement.pdf"}
        response = client.post("/api/v1/analysis/appraise", json=payload)
        
        assert response.status_code == 200
        data = response.json()

        # Required fields check
        required_keys = {"status", "appraisal_id", "individual_agent_outputs", "combined_decision", "evidence_trail", "explanation", "record_id"}
        for key in required_keys:
            assert key in data

        # Field types verification
        assert isinstance(data["status"], str)
        assert isinstance(data["appraisal_id"], str)
        assert isinstance(data["individual_agent_outputs"], dict)
        assert isinstance(data["combined_decision"], dict)
        assert isinstance(data["evidence_trail"], list)
        assert isinstance(data["explanation"], str)
        assert isinstance(data["record_id"], str)

        # Unexpected fields check
        actual_keys = set(data.keys())
        unexpected_keys = actual_keys - required_keys
        assert not unexpected_keys, f"Unexpected fields returned: {unexpected_keys}"

# ---------------------------------------------------------------------------
# Regression Tests (Existing Endpoints Keep Working)
# ---------------------------------------------------------------------------

def test_regression_get_health(client):
    """Ensure that GET /health remains functional and backward compatible."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["healthy", "degraded"]

def test_regression_get_financial_health(client):
    """Ensure that GET /api/v1/analysis/financial-health remains functional."""
    response = client.get("/api/v1/analysis/financial-health?company_name=Asenra")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["company_name"] == "Asenra"
    assert "financial_health_score" in data
    assert "ratios" in data

def test_regression_get_management_quality(client):
    """Ensure that GET /api/v1/analysis/management-quality remains functional."""
    response = client.get("/api/v1/analysis/management-quality?company_name=Asenra")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["company_name"] == "Asenra"
    assert "management_score" in data
    assert "promoter_analysis" in data
