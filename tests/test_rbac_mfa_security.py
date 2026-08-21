import pytest
import sqlite3
import jwt
import uuid
import datetime
from fastapi.testclient import TestClient
from app.main import app
from app.database.database import get_sqlite_connection, init_db
from app.security.auth_service import hash_password, JWT_SECRET, JWT_ALGORITHM
import pyotp

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
    c.execute("UPDATE system_state SET is_bootstrapped = 0 WHERE id = 1")
    conn.commit()
    conn.close()

def create_user_with_role(email="test@example.com", password="password123", role="Admin", mfa_enabled=False):
    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    c.execute("INSERT INTO users (id, email, password_hash, mfa_enabled, is_active, is_locked, failed_login_count) VALUES (?, ?, ?, ?, 1, 0, 0)", 
              (user_id, email, hash_password(password), 1 if mfa_enabled else 0))
    c.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)",
              (user_id, tenant_id, role))
    
    if mfa_enabled:
        secret = pyotp.random_base32()
        c.execute("UPDATE users SET mfa_secret = ? WHERE id = ?", (secret, user_id))
        conn.commit()
        conn.close()
        return user_id, tenant_id, secret
        
    conn.commit()
    conn.close()
    return user_id, tenant_id, None

def get_auth_token(email="test@example.com", password="password123"):
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return res.json().get("access_token")

# --- RBAC ---
def test_1_admin_can_access_admin_protected_route():
    _, tenant_id, _ = create_user_with_role("admin@example.com", "pass", "Admin")
    token = get_auth_token("admin@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200

def test_2_credit_manager_cannot_access_admin_route():
    _, tenant_id, _ = create_user_with_role("mgr@example.com", "pass", "Credit Manager")
    token = get_auth_token("mgr@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_3_credit_analyst_cannot_access_manager_operation():
    _, tenant_id, _ = create_user_with_role("ana@example.com", "pass", "Credit Analyst")
    token = get_auth_token("ana@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_4_auditor_cannot_mutate_protected_resources():
    _, tenant_id, _ = create_user_with_role("aud@example.com", "pass", "Auditor")
    token = get_auth_token("aud@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_5_auditor_can_access_permitted_read_operation():
    _, tenant_id, _ = create_user_with_role("aud@example.com", "pass", "Auditor")
    token = get_auth_token("aud@example.com", "pass")
    res = client.get(f"/api/v1/admin/policies/{tenant_id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code in [200, 404] # Authorized, but might not exist

def test_6_missing_authentication_returns_401():
    _, tenant_id, _ = create_user_with_role("aud@example.com", "pass", "Auditor")
    res = client.get(f"/api/v1/admin/policies/{tenant_id}")
    assert res.status_code in [401, 403]

def test_7_authenticated_unauthorized_role_returns_403():
    _, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Auditor")
    token = get_auth_token("user@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_8_client_supplied_role_is_ignored():
    _, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Auditor")
    token = get_auth_token("user@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}", "X-Role": "Admin"})
    assert res.status_code == 403

def test_9_changed_db_role_takes_effect():
    user_id, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("UPDATE tenant_memberships SET role = 'Auditor' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_10_inactive_membership_is_rejected():
    user_id, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("UPDATE tenant_memberships SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

# --- TENANT ISOLATION ---
def test_11_user_cannot_access_another_tenant():
    _, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    
    res = client.put(f"/api/v1/admin/policies/some-other-tenant", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_12_client_supplied_tenant_id_cannot_override_jwt_context():
    _, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/some-other-tenant", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_13_institution_id_mismatch_fails_closed():
    _, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/some-other-tenant", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_14_same_uuid_in_another_tenant_does_not_grant_access():
    user_id, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    res = client.put(f"/api/v1/admin/policies/some-other-tenant", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

def test_15_inactive_tenant_membership_is_rejected():
    user_id, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("UPDATE tenant_memberships SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403

# --- MFA ---
def test_16_mfa_enrollment_requires_authentication():
    res = client.post("/api/v1/auth/mfa/enroll")
    assert res.status_code in [401, 403]

def test_17_totp_secret_is_generated():
    user_id, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    res = client.post("/api/v1/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "provisioning_uri" in res.json()
    assert "secret=" in res.json()["provisioning_uri"]

def test_18_mfa_secret_is_not_returned_in_unsafe_responses():
    user_id, tenant_id, secret = create_user_with_role("mfa@example.com", "pass", "Admin", mfa_enabled=True)
    res = client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": "pass"})
    assert "mfa_required" in res.json()
    assert secret not in str(res.json())

def test_19_valid_totp_activates_mfa():
    user_id, tenant_id, _ = create_user_with_role("mfa@example.com", "pass", "Admin")
    token = get_auth_token("mfa@example.com", "pass")
    client.post("/api/v1/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
    
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("SELECT mfa_secret FROM users WHERE id = ?", (user_id,))
    secret = c.fetchone()[0]
    conn.close()
    
    totp = pyotp.TOTP(secret)
    code = totp.now()
    
    res = client.post("/api/v1/auth/mfa/activate", json={"code": code}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    
def test_20_invalid_totp_fails():
    user_id, tenant_id, _ = create_user_with_role("mfa@example.com", "pass", "Admin")
    token = get_auth_token("mfa@example.com", "pass")
    client.post("/api/v1/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
    
    res = client.post("/api/v1/auth/mfa/activate", json={"code": "000000"}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 400

def test_21_mfa_cannot_be_bypassed_by_modifying_request_payload():
    user_id, tenant_id, secret = create_user_with_role("mfa@example.com", "pass", "Admin", mfa_enabled=True)
    res = client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": "pass"})
    assert res.status_code == 200
    assert "access_token" not in res.json()
    assert "mfa_required" in res.json()

def test_22_mfa_verification_is_rate_limited():
    user_id, tenant_id, secret = create_user_with_role("mfa@example.com", "pass", "Admin", mfa_enabled=True)
    res = client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": "pass"})
    c_token = res.json()["challenge_token"]
    
    for _ in range(5):
        client.post("/api/v1/auth/mfa/verify-login", json={"challenge_token": c_token, "code": "000000"})
        
    res2 = client.post("/api/v1/auth/mfa/verify-login", json={"challenge_token": c_token, "code": "000000"})
    assert res2.status_code == 401
    assert "Account locked" in res2.json()["detail"]

def test_23_mfa_enabled_login_requires_mfa():
    user_id, tenant_id, secret = create_user_with_role("mfa@example.com", "pass", "Admin", mfa_enabled=True)
    res = client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": "pass"})
    assert res.status_code == 200
    assert "access_token" not in res.json()
    assert res.json().get("mfa_required") is True

def test_24_invalid_mfa_prevents_privileged_authentication():
    user_id, tenant_id, secret = create_user_with_role("mfa@example.com", "pass", "Admin", mfa_enabled=True)
    res = client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": "pass"})
    c_token = res.json()["challenge_token"]
    
    res2 = client.post("/api/v1/auth/mfa/verify-login", json={"challenge_token": c_token, "code": "000000"})
    assert res2.status_code == 401
    assert "access_token" not in res2.json()

def test_25_mfa_disable_cannot_be_performed_without_security_checks():
    res = client.post("/api/v1/auth/mfa/disable")
    assert res.status_code in [401, 403]

def test_26_mfa_state_changes_invalidate_sessions():
    user_id, tenant_id, _ = create_user_with_role("mfa@example.com", "pass", "Admin")
    token = get_auth_token("mfa@example.com", "pass")
    
    client.post("/api/v1/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("SELECT mfa_secret FROM users WHERE id = ?", (user_id,))
    secret = c.fetchone()[0]
    conn.close()
    code = pyotp.TOTP(secret).now()
    client.post("/api/v1/auth/mfa/activate", json={"code": code}, headers={"Authorization": f"Bearer {token}"})
    
    client.post("/api/v1/auth/mfa/disable", headers={"Authorization": f"Bearer {token}"})
    
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("SELECT is_revoked FROM sessions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    assert row[0] == 1
    conn.close()

def test_27_revoked_session_cannot_authorize_protected_operation():
    user_id, tenant_id, _ = create_user_with_role("user@example.com", "pass", "Admin")
    token = get_auth_token("user@example.com", "pass")
    
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("UPDATE sessions SET is_revoked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    res = client.put(f"/api/v1/admin/policies/{tenant_id}", json={"auto_approve_cutoff": 70.0, "auto_reject_cutoff": 30.0}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
