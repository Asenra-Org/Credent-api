import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database.database import get_policy, save_policy, update_appraisal_status

client = TestClient(app)

def test_get_default_policy():
    response = client.get("/api/v1/policies/DEFAULT")
    assert response.status_code == 200
    data = response.json()
    assert data["institution_id"] == "DEFAULT"
    assert "current_ratio_safe" in data
    assert "auto_approve_cutoff" in data

def test_get_admin_default_policy():
    response = client.get("/api/v1/admin/policies")
    assert response.status_code == 200
    data = response.json()
    assert data["institution_id"] == "DEFAULT"

def test_update_and_get_custom_policy():
    policy_payload = {
        "current_ratio_safe": 1.5,
        "current_ratio_min": 1.1,
        "dscr_safe": 1.3,
        "dscr_min": 1.05,
        "de_high": 1.8,
        "auto_approve_cutoff": 75.0,
        "auto_reject_cutoff": 35.0,
        "penalty_weights": {
            "integrity_mismatch": 20.0,
            "promoter_flags": 12.0
        }
    }
    put_resp = client.put("/api/v1/policies/INST_TEST_01", json=policy_payload)
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "success"

    get_resp = client.get("/api/v1/policies/INST_TEST_01")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["institution_id"] == "INST_TEST_01"
    assert data["current_ratio_safe"] == 1.5
    assert data["auto_approve_cutoff"] == 75.0

def test_update_admin_policy():
    policy_payload = {
        "current_ratio_safe": 1.6,
        "auto_approve_cutoff": 80.0,
        "auto_reject_cutoff": 30.0
    }
    put_resp = client.put("/api/v1/admin/policies", json=policy_payload)
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "success"

    get_resp = client.get("/api/v1/admin/policies")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["auto_approve_cutoff"] == 80.0

def test_invalid_policy_cutoffs():
    invalid_payload = {
        "auto_approve_cutoff": 40.0,
        "auto_reject_cutoff": 60.0
    }
    response = client.put("/api/v1/policies/INST_INVALID", json=invalid_payload)
    assert response.status_code == 400
    assert "greater than" in response.json()["detail"]

def test_dual_write_update_status():
    # Test dual-write update helper
    success = update_appraisal_status("APPRAISAL_TEST_99", "APPROVE", "Manager manual override test.")
    assert success is True
