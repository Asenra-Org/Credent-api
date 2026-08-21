# =============================================================================
# CREDENT — ASE-61: Human Approval Workflow Audit Persistence Tests
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
import os
import pytest
import sqlite3
from fastapi.testclient import TestClient

os.environ.setdefault("AUDIT_HMAC_SECRET", "test_secret_for_audit_hmac_1234567890")

from app.routes.reports import StatusUpdate
from app.database.database import (
    init_db,
    save_appraisal,
    update_appraisal_status,
    get_recent_appraisals,
    get_sqlite_connection
)

def test_status_update_pydantic_model_defaults():
    """Verify StatusUpdate model defaults when override fields are absent (backward compatibility)."""
    update = StatusUpdate(decision="APPROVE", rationale="Meets DSCR and solvency thresholds.")
    assert update.decision == "APPROVE"
    assert update.rationale == "Meets DSCR and solvency thresholds."
    assert update.override_reason is None
    assert update.is_override is False

def test_status_update_pydantic_model_with_override_and_frontend_extras():
    """Verify StatusUpdate parses override fields and tolerates additional frontend audit metadata."""
    payload = {
        "decision": "APPROVE",
        "rationale": "High business potential despite initial AI flag.",
        "override_reason": "Executive credit committee exception approved under policy clause 4.2.",
        "is_override": True,
        "officer_decision": "APPROVE",
        "ai_recommendation": "REJECT",
        "timestamp": "2026-08-21T00:55:00Z"
    }
    update = StatusUpdate(**payload)
    assert update.decision == "APPROVE"
    assert update.rationale == "High business potential despite initial AI flag."
    assert update.override_reason == "Executive credit committee exception approved under policy clause 4.2."
    assert update.is_override is True

def test_dual_write_update_status_no_override():
    """Verify update_appraisal_status persists normal decision without override."""
    appraisal_id = "APPRAISAL_NORMAL_001"

    # Pre-populate record in SQLite
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO appraisal_records (id, company_id, decision, decision_rationale) VALUES (?, ?, ?, ?)",
        (appraisal_id, "COMP_001", "PENDING", "Initial pending state")
    )
    conn.commit()
    conn.close()

    success = update_appraisal_status(
        appraisal_id=appraisal_id,
        decision="APPROVE",
        rationale="Standard approval based on clear metrics."
    )
    assert success is True

    # Verify SQLite record
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT decision, decision_rationale, override_reason, is_override FROM appraisal_records WHERE id = ?", (appraisal_id,))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "APPROVE"
    assert row[1] == "Standard approval based on clear metrics."
    assert row[2] is None
    assert row[3] == 0

def test_dual_write_update_status_with_override():
    """Verify update_appraisal_status persists structured override fields."""
    appraisal_id = "APPRAISAL_OVERRIDE_002"

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO appraisal_records (id, company_id, decision, decision_rationale) VALUES (?, ?, ?, ?)",
        (appraisal_id, "COMP_002", "REJECT", "AI flagged promoter litigation")
    )
    conn.commit()
    conn.close()

    success = update_appraisal_status(
        appraisal_id=appraisal_id,
        decision="APPROVE",
        rationale="Overriding AI recommendation after legal clearance.",
        override_reason="Litigation dismissed by High Court as per certified order copy.",
        is_override=True
    )
    assert success is True

    # Verify structured fields in SQLite
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT decision, decision_rationale, override_reason, is_override FROM appraisal_records WHERE id = ?", (appraisal_id,))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "APPROVE"
    assert row[1] == "Overriding AI recommendation after legal clearance."
    assert row[2] == "Litigation dismissed by High Court as per certified order copy."
    assert row[3] == 1

def test_patch_update_status_route_no_override(client, admin_headers):
    """Verify PATCH /api/v1/reports/update-status/{id} handles backward-compatible payload without override."""
    appraisal_id = "APPRAISAL_ROUTE_003"

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO appraisal_records (id, company_id, decision, decision_rationale, institution_id) VALUES (?, ?, ?, ?, 'DEFAULT')",
        (appraisal_id, "COMP_003", "PENDING", "Pending review")
    )
    conn.commit()
    conn.close()

    payload = {
        "decision": "REJECT",
        "rationale": "Current ratio below policy threshold."
    }
    response = client.patch(f"/api/v1/reports/update-status/{appraisal_id}", json=payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "REJECTED" in data["message"]

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT decision, decision_rationale, override_reason, is_override FROM appraisal_records WHERE id = ?", (appraisal_id,))
    row = cursor.fetchone()
    conn.close()

    assert row[0] == "REJECT"
    assert row[1] == "Current ratio below policy threshold."
    assert row[2] is None
    assert row[3] == 0

def test_patch_update_status_route_with_override(client, admin_headers):
    """Verify PATCH /api/v1/reports/update-status/{id} persists override_reason and is_override structured data."""
    appraisal_id = "APPRAISAL_ROUTE_004"

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO appraisal_records (id, company_id, decision, decision_rationale, institution_id) VALUES (?, ?, ?, ?, 'DEFAULT')",
        (appraisal_id, "COMP_004", "REJECT", "Low initial credit score")
    )
    conn.commit()
    conn.close()

    payload = {
        "decision": "APPROVE",
        "rationale": "Collateral coverage ratio exceeds 200%.",
        "override_reason": "High-value commercial real estate collateral pledged.",
        "is_override": True,
        "officer_decision": "APPROVE",
        "ai_recommendation": "REJECT",
        "timestamp": "2026-08-21T00:55:30Z"
    }
    response = client.patch(f"/api/v1/reports/update-status/{appraisal_id}", json=payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "APPROVED" in data["message"]

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT decision, decision_rationale, override_reason, is_override FROM appraisal_records WHERE id = ?", (appraisal_id,))
    row = cursor.fetchone()
    conn.close()

    assert row[0] == "APPROVE"
    assert row[1] == "Collateral coverage ratio exceeds 200%."
    assert row[2] == "High-value commercial real estate collateral pledged."
    assert row[3] == 1
