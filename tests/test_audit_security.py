import pytest
import sqlite3
import os
import uuid
import json
from datetime import datetime, timezone

from app.security.audit_service import create_audit_event, verify_tenant_chain, calculate_hmac, canonicalize
from app.database.database import init_db, get_sqlite_connection

# Force test secret
os.environ["AUDIT_HMAC_SECRET"] = "test-secret-for-audit-chain-testing"

@pytest.fixture(autouse=True)
def setup_and_teardown_db():
    init_db()
    conn = get_sqlite_connection()
    c = conn.cursor()
    # Drop triggers so tests can simulate raw DB tampering
    c.execute("DROP TRIGGER IF EXISTS prevent_audit_logs_update")
    c.execute("DROP TRIGGER IF EXISTS prevent_audit_logs_delete")
    c.execute("DELETE FROM audit_logs")
    c.execute("DELETE FROM audit_chain_heads")
    conn.commit()
    conn.close()
    yield

def test_audit_creation_and_chain_validity():
    tenant_id = "TENANT_1"
    user_id = "USER_1"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Event 1
        create_audit_event(conn, tenant_id, user_id, "ACTION_1")
        # Event 2
        create_audit_event(conn, tenant_id, user_id, "ACTION_2")
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "valid"
    assert res["length"] == 2

def test_tamper_previous_state():
    tenant_id = "TENANT_2"
    user_id = "USER_1"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event_id = create_audit_event(conn, tenant_id, user_id, "ACTION_1", previous_state={"foo": "bar"})
        conn.commit()
        
        # Tamper without recalculating HMAC
        conn.execute("UPDATE audit_logs SET previous_state = ? WHERE id = ?", (json.dumps({"foo": "tampered"}), event_id))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "HMAC mismatch" in res["reason"]

def test_tamper_current_hash():
    tenant_id = "TENANT_3"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event_id = create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        conn.commit()
        
        # Tamper current hash
        conn.execute("UPDATE audit_logs SET current_hash = ? WHERE id = ?", ("fakedhash123", event_id))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "HMAC mismatch" in res["reason"]

def test_tamper_previous_hash():
    tenant_id = "TENANT_4"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        event_id_2 = create_audit_event(conn, tenant_id, "U1", "ACTION_2")
        conn.commit()
        
        # Tamper previous hash
        conn.execute("UPDATE audit_logs SET previous_hash = ? WHERE id = ?", ("fakedhash123", event_id_2))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "Broken link" in res["reason"]

def test_recalculate_without_hmac_secret_fails():
    tenant_id = "TENANT_5"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event_id = create_audit_event(conn, tenant_id, "U1", "ACTION_1", reason="Initial")
        conn.commit()
        
        # Attacker modifies reason and recalculates standard SHA256 (no HMAC secret)
        c = conn.cursor()
        c.execute("SELECT previous_hash FROM audit_logs WHERE id=?", (event_id,))
        prev_hash = c.fetchone()[0]
        
        fake_payload = canonicalize({
            "tenant_id": tenant_id,
            "user_id": "U1",
            "case_id": None,
            "action": "ACTION_1",
            "resource_type": None,
            "resource_id": None,
            "previous_state": None,
            "new_state": None,
            "decision": None,
            "reason": "Hacked",
            "sequence_number": 1
        })
        
        import hashlib
        data = (fake_payload + prev_hash).encode("utf-8")
        fake_hash = hashlib.sha256(data).hexdigest()
        
        conn.execute("UPDATE audit_logs SET reason = ?, current_hash = ? WHERE id = ?", ("Hacked", fake_hash, event_id))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "HMAC mismatch" in res["reason"]

def test_middle_event_deletion_detected():
    tenant_id = "TENANT_6"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        event_2 = create_audit_event(conn, tenant_id, "U1", "ACTION_2")
        create_audit_event(conn, tenant_id, "U1", "ACTION_3")
        conn.commit()
        
        # Attacker deletes middle event
        conn.execute("DELETE FROM audit_logs WHERE id = ?", (event_2,))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "Sequence gap" in res["reason"]

def test_tail_event_deletion_detected():
    tenant_id = "TENANT_7"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        event_2 = create_audit_event(conn, tenant_id, "U1", "ACTION_2")
        conn.commit()
        
        # Attacker deletes tail event
        conn.execute("DELETE FROM audit_logs WHERE id = ?", (event_2,))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "Chain head sequence mismatch" in res["reason"]

def test_fail_closed_transaction_rollback():
    tenant_id = "TENANT_8"
    
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO companies (id, name, sector) VALUES ('TEST_C', 'Test Co', 'Tech')")
        
        # Simulate audit failure (e.g. missing param causing exception)
        try:
            create_audit_event(conn, tenant_id, "", "") # Invalid args raise ValueError
            conn.commit()
        except ValueError:
            conn.rollback()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("SELECT count(*) FROM companies WHERE id='TEST_C'")
    assert c.fetchone()[0] == 0, "Business mutation should have rolled back"
    
    c.execute("SELECT count(*) FROM audit_logs WHERE tenant_id=?", (tenant_id,))
    assert c.fetchone()[0] == 0, "Audit event should not exist"
    
    c.execute("SELECT count(*) FROM audit_chain_heads WHERE tenant_id=?", (tenant_id,))
    assert c.fetchone()[0] == 0, "Chain head should not exist"
    conn.close()

def test_missing_hmac_secret_fails_closed():
    # Remove secret from env
    old_secret = os.environ.pop("AUDIT_HMAC_SECRET", None)
    
    tenant_id = "TENANT_9"
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError) as excinfo:
            create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        assert "AUDIT_HMAC_SECRET is not set" in str(excinfo.value)
    finally:
        conn.close()
        if old_secret:
            os.environ["AUDIT_HMAC_SECRET"] = old_secret

def test_reordered_sequence():
    tenant_id = "TENANT_REORDER"
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ev1 = create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        ev2 = create_audit_event(conn, tenant_id, "U1", "ACTION_2")
        conn.commit()
        
        # Swap sequence numbers avoiding unique constraint violation
        conn.execute("UPDATE audit_logs SET sequence_number = 999 WHERE id = ?", (ev1,))
        conn.execute("UPDATE audit_logs SET sequence_number = 1 WHERE id = ?", (ev2,))
        conn.execute("UPDATE audit_logs SET sequence_number = 2 WHERE id = ?", (ev1,))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "Sequence gap" in res["reason"] or "HMAC mismatch" in res["reason"] or "Broken link" in res["reason"]

def test_chain_head_manipulation():
    tenant_id = "TENANT_CHAINHEAD"
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        conn.commit()
        
        # Modify chain head
        conn.execute("UPDATE audit_chain_heads SET latest_hash = 'fake' WHERE tenant_id = ?", (tenant_id,))
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "invalid"
    assert "Chain head hash mismatch" in res["reason"]

def test_cross_tenant_chain_isolation():
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_audit_event(conn, "TENANT_A", "U1", "ACTION_A")
        create_audit_event(conn, "TENANT_B", "U1", "ACTION_B")
        conn.commit()
    finally:
        conn.close()
        
    conn = get_sqlite_connection()
    # verify_tenant_chain should only see TENANT_A's events and head
    res_a = verify_tenant_chain(conn, "TENANT_A")
    res_b = verify_tenant_chain(conn, "TENANT_B")
    conn.close()
    
    assert res_a["status"] == "valid"
    assert res_a["length"] == 1
    assert res_b["status"] == "valid"
    assert res_b["length"] == 1

def test_concurrent_writes():
    import threading
    tenant_id = "TENANT_CONCURRENT"
    
    def worker():
        # Each worker creates its own connection and executes a transaction
        conn = get_sqlite_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            create_audit_event(conn, tenant_id, "U1", "ACTION")
            conn.commit()
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    conn = get_sqlite_connection()
    res = verify_tenant_chain(conn, tenant_id)
    conn.close()
    
    assert res["status"] == "valid"
    assert res["length"] == 5

def test_secret_non_disclosure():
    tenant_id = "TENANT_SECRET"
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_audit_event(conn, tenant_id, "U1", "ACTION_1")
        conn.commit()
        
        cursor = conn.cursor()
        cursor.execute("SELECT previous_hash, current_hash FROM audit_logs WHERE tenant_id=?", (tenant_id,))
        row = cursor.fetchone()
        
        # Secret should not appear in hashes
        secret = os.environ.get("AUDIT_HMAC_SECRET", "")
        assert secret not in row[0]
        assert secret not in row[1]
    finally:
        conn.close()
