import os
import json
import uuid
import hmac
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import sqlite3

def get_hmac_secret() -> bytes:
    secret = os.getenv("AUDIT_HMAC_SECRET")
    if not secret:
        raise RuntimeError("AUDIT_HMAC_SECRET is not set in environment. Failing closed.")
    return secret.encode("utf-8")

def canonicalize(event: Dict[str, Any]) -> str:
    """
    Produce a stable, deterministic JSON representation.
    We only include fields that are cryptographically verified.
    """
    keys_to_hash = [
        "tenant_id", "user_id", "case_id", "action", "resource_type",
        "resource_id", "previous_state", "new_state", "decision", 
        "reason", "sequence_number", "timestamp"
    ]
    
    # Extract only required keys, handling Nones explicitly
    stable_dict = {}
    for k in keys_to_hash:
        val = event.get(k)
        if val is None:
            stable_dict[k] = None
        elif isinstance(val, dict) or isinstance(val, list):
            # Sort inner dictionaries for stable serialization
            stable_dict[k] = val
        else:
            stable_dict[k] = str(val)

    # Convert to JSON with sorted keys, no whitespace for exact reproducibility
    return json.dumps(stable_dict, sort_keys=True, separators=(',', ':'))

def calculate_hmac(canonical_payload: str, previous_hash: str) -> str:
    """
    Calculate HMAC-SHA256 for the event.
    Formula: HMAC(secret, canonical_payload + previous_hash)
    """
    secret = get_hmac_secret()
    data = (canonical_payload + previous_hash).encode("utf-8")
    return hmac.new(secret, data, hashlib.sha256).hexdigest()

def create_audit_event(
    conn: sqlite3.Connection,
    tenant_id: str,
    user_id: str,
    action: str,
    case_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    previous_state: Optional[Dict[str, Any]] = None,
    new_state: Optional[Dict[str, Any]] = None,
    decision: Optional[str] = None,
    reason: Optional[str] = None,
    timestamp_override: Optional[datetime] = None
) -> str:
    """
    Create a new audit event, cryptographically linked to the previous event for this tenant.
    Updates the chain head within the same transaction.
    Must be called inside an active SQLite transaction. `conn` must NOT be autocommit.
    """
    if not tenant_id or not user_id or not action:
        raise ValueError("tenant_id, user_id, and action are required for audit logging.")

    cursor = conn.cursor()
    cursor.execute('''SELECT latest_sequence, latest_hash FROM audit_chain_heads 
                      WHERE tenant_id = ?''', (tenant_id,))
    row = cursor.fetchone()
    
    if row:
        latest_sequence, latest_hash = row
        next_sequence = latest_sequence + 1
        previous_hash = latest_hash
    else:
        next_sequence = 1
        # Deterministic genesis previous_hash (all zeros, length 64 for SHA-256)
        previous_hash = "0" * 64

    ts = timestamp_override or datetime.now(timezone.utc)
    ts_iso = ts.isoformat()

    # Build the event dictionary for canonicalization
    event_data = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "case_id": case_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "previous_state": previous_state,
        "new_state": new_state,
        "decision": decision,
        "reason": reason,
        "sequence_number": next_sequence,
        "timestamp": ts_iso
    }

    # Canonicalize and Hash
    canonical_payload = canonicalize(event_data)
    current_hash = calculate_hmac(canonical_payload, previous_hash)
    
    event_id = str(uuid.uuid4())

    # Insert into audit_logs
    cursor.execute('''
        INSERT INTO audit_logs (
            id, tenant_id, user_id, case_id, action, resource_type, resource_id,
            previous_state, new_state, decision, reason, sequence_number,
            previous_hash, current_hash, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        event_id, tenant_id, user_id, case_id, action, resource_type, resource_id,
        json.dumps(previous_state) if previous_state is not None else None,
        json.dumps(new_state) if new_state is not None else None,
        decision, reason, next_sequence, previous_hash, current_hash, ts_iso
    ))

    # Update chain head
    cursor.execute('''
        INSERT INTO audit_chain_heads (tenant_id, latest_sequence, latest_hash, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tenant_id) DO UPDATE SET
            latest_sequence=excluded.latest_sequence,
            latest_hash=excluded.latest_hash,
            updated_at=excluded.updated_at
    ''', (tenant_id, next_sequence, current_hash, ts_iso))

    return event_id

def verify_tenant_chain(conn: sqlite3.Connection, tenant_id: str) -> Dict[str, Any]:
    """
    Verify the cryptographic integrity of a tenant's entire audit chain.
    """
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_id, case_id, action, resource_type, resource_id,
               previous_state, new_state, decision, reason, sequence_number,
               previous_hash, current_hash, timestamp
        FROM audit_logs
        WHERE tenant_id = ?
        ORDER BY sequence_number ASC
    ''', (tenant_id,))
    
    logs = cursor.fetchall()
    
    cursor.execute('''SELECT latest_sequence, latest_hash FROM audit_chain_heads 
                      WHERE tenant_id = ?''', (tenant_id,))
    head_row = cursor.fetchone()
    
    if not logs and not head_row:
        return {"status": "valid", "reason": "Empty chain"}
    if not logs and head_row:
        return {"status": "invalid", "reason": "Chain head exists but no logs found"}
    if logs and not head_row:
        return {"status": "invalid", "reason": "Logs exist but no chain head found"}
    
    expected_previous_hash = "0" * 64
    expected_sequence = 1
    
    for row in logs:
        (event_id, user_id, case_id, action, resource_type, resource_id,
         prev_state_raw, new_state_raw, decision, reason, seq_num,
         prev_hash, curr_hash, ts) = row
        
        # 1. Check sequence number
        if seq_num != expected_sequence:
            return {"status": "invalid", "reason": f"Sequence gap/mismatch at {seq_num}, expected {expected_sequence}"}
        
        # 2. Check previous_hash link
        if prev_hash != expected_previous_hash:
            return {"status": "invalid", "reason": f"Broken link at sequence {seq_num}: previous_hash mismatch"}
        
        # 3. Recalculate HMAC and verify current_hash
        event_data = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "case_id": case_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "previous_state": json.loads(prev_state_raw) if prev_state_raw else None,
            "new_state": json.loads(new_state_raw) if new_state_raw else None,
            "decision": decision,
            "reason": reason,
            "sequence_number": seq_num,
            "timestamp": ts
        }
        
        canonical_payload = canonicalize(event_data)
        try:
            calculated_hash = calculate_hmac(canonical_payload, prev_hash)
        except RuntimeError as e:
            return {"status": "error", "reason": str(e)}
        
        if calculated_hash != curr_hash:
            return {"status": "invalid", "reason": f"HMAC mismatch at sequence {seq_num}: {curr_hash} != {calculated_hash}"}
        
        expected_previous_hash = curr_hash
        expected_sequence += 1
        
    # 4. Check chain head
    latest_sequence, latest_hash = head_row
    if latest_sequence != expected_sequence - 1:
        return {"status": "invalid", "reason": f"Chain head sequence mismatch: {latest_sequence} != {expected_sequence - 1}"}
    if latest_hash != expected_previous_hash:
        return {"status": "invalid", "reason": f"Chain head hash mismatch: {latest_hash} != {expected_previous_hash}"}
        
    return {"status": "valid", "reason": "Chain intact", "length": len(logs)}
