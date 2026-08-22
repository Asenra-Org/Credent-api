import pytest
import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.database.database import get_policy, save_policy, update_appraisal_status, get_sqlite_connection, init_db
from app.security.auth_service import hash_password

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()

@pytest.fixture(autouse=True)
def set_audit_hmac_secret(monkeypatch):
    monkeypatch.setenv("AUDIT_HMAC_SECRET", "dummy_test_secret_for_audit_logs")

@pytest.fixture(autouse=True)
def cleanup_db():
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("DELETE FROM sessions")
    c.execute("DELETE FROM tenant_memberships")
    c.execute("DELETE FROM users")
    c.execute("DELETE FROM institution_policies WHERE institution_id != 'DEFAULT'")
    conn.commit()
    conn.close()
    client.cookies.clear()

def create_user_and_get_token(email=None, password="password123", role="ORG_ADMIN", tenant_id=None):
    if not email:
        email = f"test_{uuid.uuid4()}@example.com"
    if not tenant_id:
        tenant_id = str(uuid.uuid4())

    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    c.execute("INSERT INTO users (id, email, password_hash, mfa_enabled, is_active, is_locked, failed_login_count) VALUES (?, ?, ?, 0, 1, 0, 0)",
              (user_id, email, hash_password(password)))
    c.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)",
              (user_id, tenant_id, role))
    conn.commit()
    conn.close()

    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return res.json().get("access_token"), tenant_id

def test_anonymous_access_denied():
    assert client.get("/api/v1/policies/DEFAULT").status_code in [401, 403]
    assert client.get("/api/v1/admin/policies").status_code in [401, 403]
    assert client.put("/api/v1/admin/policies", json={"auto_approve_cutoff": 80.0, "auto_reject_cutoff": 30.0}).status_code in [401, 403]

def test_get_default_policy():
    token, _ = create_user_and_get_token(role="CREDIT_ANALYST")
    response = client.get("/api/v1/policies/DEFAULT", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["institution_id"] == "DEFAULT"
    assert "current_ratio_safe" in data
    assert "auto_approve_cutoff" in data

def test_get_admin_default_policy():
    token, _ = create_user_and_get_token(role="VIEWER")
    response = client.get("/api/v1/admin/policies", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["institution_id"] == "DEFAULT"

def test_update_and_get_custom_policy():
    token, tenant_id = create_user_and_get_token(role="ORG_ADMIN", tenant_id="INST_TEST_01")
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
    put_resp = client.put("/api/v1/policies/INST_TEST_01", json=policy_payload, headers={"Authorization": f"Bearer {token}"})
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "success"

    get_resp = client.get("/api/v1/policies/INST_TEST_01", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["institution_id"] == "INST_TEST_01"
    assert data["current_ratio_safe"] == 1.5
    assert data["auto_approve_cutoff"] == 75.0

def test_update_admin_policy():
    token, tenant_id = create_user_and_get_token(role="ORG_ADMIN", tenant_id="DEFAULT")
    policy_payload = {
        "current_ratio_safe": 1.6,
        "auto_approve_cutoff": 80.0,
        "auto_reject_cutoff": 30.0
    }
    put_resp = client.put("/api/v1/admin/policies", json=policy_payload, headers={"Authorization": f"Bearer {token}"})
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "success"

    get_resp = client.get("/api/v1/admin/policies", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["auto_approve_cutoff"] == 80.0

def test_invalid_policy_cutoffs():
    token, tenant_id = create_user_and_get_token(role="ORG_ADMIN", tenant_id="INST_INVALID")
    invalid_payload = {
        "auto_approve_cutoff": 40.0,
        "auto_reject_cutoff": 60.0
    }
    response = client.put("/api/v1/policies/INST_INVALID", json=invalid_payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "greater than" in response.json()["detail"]

def test_dual_write_update_status():
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO appraisal_records (id, institution_id) VALUES (?, 'DEFAULT')", ("APPRAISAL_TEST_99",))
    conn.commit()
    conn.close()
    
    success = update_appraisal_status("APPRAISAL_TEST_99", "APPROVE", "Manager manual override test.")
    assert success is True
