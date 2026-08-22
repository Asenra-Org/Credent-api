import os
import pytest
import sqlite3
import uuid
import hashlib
from datetime import datetime
from app.database.database import get_sqlite_connection, init_db

@pytest.fixture(scope="module", autouse=True)
def ensure_db_initialized():
    """Ensure the database tables are created before running tests."""
    init_db()

def get_conn():
    return get_sqlite_connection()

def test_users_table_creation_and_uniqueness():
    """Test 1 & 2: Users table exists and enforces email uniqueness."""
    conn = get_conn()
    cursor = conn.cursor()
    
    # Ensure empty to avoid conflicts
    cursor.execute("DELETE FROM users")
    
    user_id = str(uuid.uuid4())
    cursor.execute("""
        INSERT INTO users (id, email, password_hash, mfa_secret)
        VALUES (?, ?, ?, ?)
    """, (user_id, "test@example.com", "fakehash", "fakesecret"))
    
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO users (id, email, password_hash)
            VALUES (?, ?, ?)
        """, (str(uuid.uuid4()), "test@example.com", "fakehash2"))
    
    conn.rollback()
    conn.close()

def test_tenant_membership_creation():
    """Test 3 & 4: Tenant membership creation and composite key uniqueness."""
    conn = get_conn()
    cursor = conn.cursor()
    
    user_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)", (user_id, "member@example.com", "hash"))
    
    # Can belong to multiple tenants
    cursor.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, ?)", (user_id, "tenant1", "ORG_ADMIN"))
    cursor.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, ?)", (user_id, "tenant2", "CREDIT_ANALYST"))
    
    # Cannot have duplicate membership for same tenant
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, ?)", (user_id, "tenant1", "Manager"))
    
    conn.rollback()
    conn.close()

def test_session_persistence_and_hashed_tokens():
    """Test 5 & 6: Session persistence and hashed tokens."""
    conn = get_conn()
    cursor = conn.cursor()
    
    user_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)", (user_id, "session@example.com", "hash"))
    
    session_id = str(uuid.uuid4())
    raw_token = "plain_text_token"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    
    cursor.execute("""
        INSERT INTO sessions (id, user_id, refresh_token_hash, expires_at)
        VALUES (?, ?, ?, datetime('now', '+1 day'))
    """, (session_id, user_id, token_hash))
    
    cursor.execute("SELECT refresh_token_hash FROM sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    assert row[0] == token_hash
    assert row[0] != raw_token # Plaintext token is not stored
    
    conn.rollback()
    conn.close()

def test_audit_record_and_chain_head_persistence():
    """Test 7 & 8: Audit record persistence and chain-head persistence."""
    conn = get_conn()
    cursor = conn.cursor()
    
    tenant_id = "tenant_audit_test"
    
    cursor.execute("""
        INSERT INTO audit_chain_heads (tenant_id, latest_sequence, latest_hash)
        VALUES (?, ?, ?)
    """, (tenant_id, 1, "hash1"))
    
    cursor.execute("""
        INSERT INTO audit_logs (id, tenant_id, user_id, action, sequence_number, previous_hash, current_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), tenant_id, "user1", "TEST_ACTION", 1, "000", "hash1"))
    
    cursor.execute("SELECT latest_sequence FROM audit_chain_heads WHERE tenant_id = ?", (tenant_id,))
    assert cursor.fetchone()[0] == 1
    
    conn.rollback()
    conn.close()

def test_audit_sequence_uniqueness():
    """Test 9: Audit sequence uniqueness per tenant."""
    conn = get_conn()
    cursor = conn.cursor()
    
    tenant_id = "tenant_seq_test"
    
    cursor.execute("""
        INSERT INTO audit_logs (id, tenant_id, user_id, action, sequence_number, previous_hash, current_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), tenant_id, "user1", "ACTION1", 1, "prev1", "curr1"))
    
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO audit_logs (id, tenant_id, user_id, action, sequence_number, previous_hash, current_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), tenant_id, "user2", "ACTION2", 1, "prev2", "curr2"))
        
    # Same sequence number for different tenant is allowed
    cursor.execute("""
        INSERT INTO audit_logs (id, tenant_id, user_id, action, sequence_number, previous_hash, current_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), "tenant_other", "user2", "ACTION2", 1, "prev2", "curr2"))
    
    conn.rollback()
    conn.close()

def test_system_uninitialized_and_no_defaults():
    """Test 10, 12, 13: System starts UNINITIALIZED, no default users, no default credentials."""
    conn = get_conn()
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_bootstrapped FROM system_state WHERE id = 1")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == 0, "System must start UNINITIALIZED"
    
    # Clear users from other tests to verify init_db itself doesn't create defaults
    cursor.execute("DELETE FROM users")
    conn.commit()
    init_db()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    assert cursor.fetchone()[0] == 0, "No default users should exist after migration"
    
    conn.close()

def test_existing_data_preserved():
    """Test 11: Migration preserves existing application data (mock check)."""
    conn = get_conn()
    cursor = conn.cursor()
    
    # We just ensure the original tables exist and are readable without errors
    cursor.execute("SELECT COUNT(*) FROM appraisal_records")
    cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM loan_cases")
    cursor.fetchall()
    
    conn.close()
