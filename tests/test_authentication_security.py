import pytest
import os
import sqlite3
import jwt
import uuid
import datetime
from fastapi.testclient import TestClient
from app.main import app
from app.database.database import get_sqlite_connection, init_db
from app.security.auth_service import hash_password, verify_password, JWT_SECRET, JWT_ALGORITHM

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()

@pytest.fixture(autouse=True)
def cleanup_db():
    # Clean up before each test
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("DELETE FROM sessions")
    c.execute("DELETE FROM tenant_memberships")
    c.execute("DELETE FROM users")
    c.execute("UPDATE system_state SET is_bootstrapped = 0 WHERE id = 1")
    conn.commit()
    conn.close()

# Helper
def create_test_user(email="test@example.com", password="password123", is_active=1, is_locked=0):
    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    c.execute("INSERT INTO users (id, email, password_hash, is_active, is_locked) VALUES (?, ?, ?, ?, ?)", 
              (user_id, email, hash_password(password), is_active, is_locked))
    c.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, ?)",
              (user_id, tenant_id, "Admin"))
    conn.commit()
    conn.close()
    return user_id, tenant_id

# --- PASSWORD SECURITY ---
def test_password_is_hashed_and_verifies():
    pwd = "MySecretPassword123"
    hashed = hash_password(pwd)
    assert hashed != pwd
    assert verify_password(pwd, hashed) is True
    assert verify_password("wrong", hashed) is False

def test_malformed_hash_fails():
    assert verify_password("pass", "malformed_hash") is False

# --- LOGIN ---
def test_valid_login_succeeds():
    create_test_user("valid@credent.com", "pass123")
    res = client.post("/api/v1/auth/login", json={"email": "valid@credent.com", "password": "pass123"})
    assert res.status_code == 200
    assert "access_token" in res.json()
    assert "refresh_token" in res.cookies

def test_invalid_password_fails():
    create_test_user("inv@credent.com", "pass123")
    res = client.post("/api/v1/auth/login", json={"email": "inv@credent.com", "password": "wrong"})
    assert res.status_code == 401
    assert "Invalid credentials" in res.json()["detail"]

def test_unknown_user_does_not_reveal_existence():
    res = client.post("/api/v1/auth/login", json={"email": "unknown@credent.com", "password": "any"})
    assert res.status_code == 401
    assert "Invalid credentials" in res.json()["detail"]

def test_locked_user_cannot_login():
    create_test_user("locked@credent.com", "pass123", is_locked=1)
    res = client.post("/api/v1/auth/login", json={"email": "locked@credent.com", "password": "pass123"})
    assert res.status_code == 401

def test_disabled_user_cannot_login():
    create_test_user("disabled@credent.com", "pass123", is_active=0)
    res = client.post("/api/v1/auth/login", json={"email": "disabled@credent.com", "password": "pass123"})
    assert res.status_code == 401

# --- JWT ---
def test_jwt_verification():
    user_id, tenant_id = create_test_user()
    res = client.post("/api/v1/auth/login", json={"email": "test@example.com", "password": "password123"})
    token = res.json()["access_token"]
    
    # Needs to be able to decode
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["sub"] == user_id
    assert payload["tenant_id"] == tenant_id

# --- REFRESH SESSIONS ---
def test_refresh_session_and_logout():
    create_test_user("ref@credent.com", "pass123")
    res = client.post("/api/v1/auth/login", json={"email": "ref@credent.com", "password": "pass123"})
    refresh_cookie = res.cookies.get("refresh_token")
    
    # Refresh
    res2 = client.post("/api/v1/auth/refresh", cookies={"refresh_token": refresh_cookie})
    assert res2.status_code == 200
    assert "access_token" in res2.json()
    
    # Logout
    res3 = client.post("/api/v1/auth/logout", cookies={"refresh_token": refresh_cookie})
    assert res3.status_code == 200
    
    # Use revoked token
    res4 = client.post("/api/v1/auth/refresh", cookies={"refresh_token": refresh_cookie})
    assert res4.status_code == 401

# --- LOCKOUT ---
def test_failed_attempts_increment_and_lockout():
    create_test_user("lockout@credent.com", "pass123")
    
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "lockout@credent.com", "password": "wrong"})
        
    # Check DB state
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("SELECT failed_login_count, is_locked FROM users WHERE email='lockout@credent.com'")
    row = c.fetchone()
    conn.close()
    
    assert row[0] == 5
    assert row[1] == 1 # locked
    
    # Try correct password while locked
    res = client.post("/api/v1/auth/login", json={"email": "lockout@credent.com", "password": "pass123"})
    assert res.status_code == 401
    
# --- BOOTSTRAP ---
def test_bootstrap_flow():
    token = os.getenv("BOOTSTRAP_TOKEN", "default-dev-bootstrap-do-not-use")
    
    # Success
    res = client.post("/api/v1/auth/bootstrap", 
                      headers={"X-Bootstrap-Token": token},
                      json={"initial_password": "super-strong-password"})
    assert res.status_code == 201
    assert res.json()["data"]["email"] == "admin@credent.local"
    
    # 2nd attempt fails
    res2 = client.post("/api/v1/auth/bootstrap", 
                      headers={"X-Bootstrap-Token": token},
                      json={"initial_password": "other-password"})
    assert res2.status_code == 403
    
def test_invalid_bootstrap_token():
    res = client.post("/api/v1/auth/bootstrap", 
                      headers={"X-Bootstrap-Token": "wrong"},
                      json={"initial_password": "super-strong-password"})
    assert res.status_code == 403

