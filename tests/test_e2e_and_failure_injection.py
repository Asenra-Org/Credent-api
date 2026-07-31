# =============================================================================
# CREDENT — Advanced E2E, Unit, Integration & Failure Injection Test Suite
# Feature: ASE-43 [BE-W5] Route Coordinator to UI, Dynamic Risk Policies & Policy Management API
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from app.database.database import get_policy, save_policy, update_appraisal_status
from app.routes.policies import PolicyRequest
from app.agents.orchestration.coordinator import AgentCoordinator

# =============================================================================
# 1. UNIT TESTS (Validators, Models & Database Helpers)
# =============================================================================

def test_policy_request_pydantic_valid():
    """Verify PolicyRequest model accepts valid risk cutoffs and boundaries."""
    req = PolicyRequest(
        auto_approve_cutoff=70.0,
        auto_reject_cutoff=30.0,
        current_ratio_safe=1.4
    )
    assert req.auto_approve_cutoff == 70.0
    assert req.auto_reject_cutoff == 30.0
    assert req.current_ratio_safe == 1.4


def test_policy_database_save_and_get():
    """Verify direct save_policy and get_policy database CRUD operations."""
    inst_id = "TEST_BANK_99"
    policy_payload = {
        "institution_id": inst_id,
        "current_ratio_safe": 1.6,
        "current_ratio_min": 1.2,
        "dscr_safe": 1.4,
        "dscr_min": 1.1,
        "de_high": 1.9,
        "auto_approve_cutoff": 80.0,
        "auto_reject_cutoff": 40.0,
        "penalty_weights": {"integrity_mismatch": 25.0}
    }
    
    # Save policy
    success = save_policy(policy_payload)
    assert success is True
    
    # Retrieve policy
    retrieved = get_policy(inst_id)
    assert retrieved is not None
    assert retrieved["institution_id"] == inst_id
    assert retrieved["auto_approve_cutoff"] == 80.0
    assert retrieved["penalty_weights"] == {"integrity_mismatch": 25.0}


def test_dual_write_status_override():
    """Verify update_appraisal_status persists status overrides."""
    appraisal_id = "TEST_APPRAISAL_888"
    success = update_appraisal_status(appraisal_id, "APPROVE", "Manual Override by Senior Underwriter")
    assert success is True

# =============================================================================
# 2. INTEGRATION TESTS (API Routes & Endpoint Contracts)
# =============================================================================

def test_get_admin_policies_route(client):
    """Verify GET /api/v1/admin/policies returns active default policy."""
    response = client.get("/api/v1/admin/policies")
    assert response.status_code == 200
    data = response.json()
    assert data["institution_id"] == "DEFAULT"
    assert "auto_approve_cutoff" in data

def test_put_admin_policies_route(client, sample_policy_payload):
    """Verify PUT /api/v1/admin/policies updates default institutional parameters."""
    response = client.put("/api/v1/admin/policies", json=sample_policy_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    
    # Re-verify GET endpoint returns updated policy
    get_res = client.get("/api/v1/admin/policies")
    policy_data = get_res.json()
    assert policy_data["auto_approve_cutoff"] == 75.0

def test_put_policies_invalid_cutoff_range(client):
    """Verify PUT policy endpoint rejects auto_approve_cutoff <= auto_reject_cutoff with HTTP 400."""
    invalid_payload = {
        "auto_approve_cutoff": 40.0,
        "auto_reject_cutoff": 60.0
    }
    response = client.put("/api/v1/admin/policies", json=invalid_payload)
    assert response.status_code == 400
    assert "greater than" in response.json()["detail"].lower()

def test_patch_update_status_route(client):
    """Verify PATCH /api/v1/reports/update-status/{id} executes status override."""
    payload = {
        "decision": "REJECT",
        "rationale": "High Debt-to-Equity ratio identified."
    }
    response = client.patch("/api/v1/reports/update-status/APPRAISAL_777", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

# =============================================================================
# 3. END-TO-END (E2E) & ORCHESTRATOR TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_coordinator_dynamic_policy_evaluation():
    """Verify AgentCoordinator loads policy dynamically and applies approval cutoffs."""
    coordinator = AgentCoordinator()
    
    # Mock ingestion and downstream agents matching exact attribute names and method signatures
    with patch.object(coordinator.ingestion_agent, "ingest_pdf") as mock_ingest, \
         patch.object(coordinator.ingestion_agent, "parse_financial_statement") as mock_parse, \
         patch.object(coordinator.financial_agent, "analyze") as mock_fin, \
         patch.object(coordinator.management_agent, "analyze") as mock_mgmt, \
         patch.object(coordinator.sector_agent, "get_sector_outlook") as mock_sec, \
         patch.object(coordinator.sector_agent, "check_rbi_policies") as mock_rbi, \
         patch.object(coordinator.integrity_agent, "cross_validate") as mock_integ, \
         patch.object(coordinator.cam_agent, "generate_cam") as mock_cam:
         
        mock_ingest.return_value = {"status": "success", "text": "Financial Statement Text Content Length > 10"}
        mock_parse.return_value = {"company_name": "Test Enterprise", "sector": "Technology"}
        mock_fin.return_value = {"financial_health_score": 85.0, "metrics": {}, "ratios": {"current_ratio": 1.4}}
        mock_mgmt.return_value = {"status": "success", "management_score": 80.0}
        mock_sec.return_value = {"outlook": "Positive"}
        mock_rbi.return_value = []
        mock_integ.return_value = {"status": "success", "discrepancies": []}
        mock_cam.return_value = {
            "decision": "APPROVED",
            "recommended_loan_amount": "5000000",
            "recommended_interest_rate": "9.5%",
            "decision_rationale": "High health score."
        }
        
        # Run appraisal with valid test file
        result = await coordinator.run_appraisal({
            "file_path": "tests/conftest.py",
            "institution_id": "DEFAULT"
        })
        
        assert result["status"] == "success"
        assert "combined_decision" in result
        assert result["combined_decision"]["decision"] == "APPROVED"

# =============================================================================
# 4. FAILURE INJECTION & RESILIENCY TESTS
# =============================================================================

def test_upload_pdf_empty_file_rejected(client):
    """Failure Injection: 0-byte PDF upload must be rejected with HTTP 400."""
    files = {"file": ("empty.pdf", b"", "application/pdf")}
    response = client.post("/api/v1/documents/ingest/pdf", files=files)
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()

def test_upload_pdf_oversized_file_rejected(client):
    """Failure Injection: > 20MB file upload must be rejected with HTTP 413."""
    large_buffer = b"0" * (21 * 1024 * 1024)
    files = {"file": ("large.pdf", large_buffer, "application/pdf")}
    response = client.post("/api/v1/documents/ingest/pdf", files=files)
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()

def test_supabase_outage_fallback_persistence():
    """Failure Injection: Simulate Supabase offline error; verify SQLite fallback succeeds."""
    with patch("app.database.database._get_supabase", return_value=None):
        success = update_appraisal_status("APPRAISAL_OFFLINE_101", "APPROVE", "Offline mode override")
        assert success is True

@pytest.mark.asyncio
async def test_llm_timeout_triggers_local_heuristic_fallback():
    """Failure Injection: Simulate CAM LLM timeout; verify local score decision fallback."""
    coordinator = AgentCoordinator()
    
    with patch.object(coordinator.ingestion_agent, "ingest_pdf") as mock_ingest, \
         patch.object(coordinator.ingestion_agent, "parse_financial_statement") as mock_parse, \
         patch.object(coordinator.financial_agent, "analyze") as mock_fin, \
         patch.object(coordinator.management_agent, "analyze") as mock_mgmt, \
         patch.object(coordinator.sector_agent, "get_sector_outlook") as mock_sec, \
         patch.object(coordinator.sector_agent, "check_rbi_policies") as mock_rbi, \
         patch.object(coordinator.integrity_agent, "cross_validate") as mock_integ, \
         patch.object(coordinator.cam_agent, "generate_cam", side_effect=TimeoutError("LLM API Timeout")):
         
        mock_ingest.return_value = {"status": "success", "text": "Valid Financial Statement Text String"}
        mock_parse.return_value = {"company_name": "Resilient Corp", "sector": "Energy"}
        mock_fin.return_value = {"financial_health_score": 85.0, "metrics": {}, "ratios": {}}
        mock_mgmt.return_value = {"status": "success", "management_score": 80.0}
        mock_sec.return_value = {"outlook": "Positive"}
        mock_rbi.return_value = []
        mock_integ.return_value = {"status": "success", "discrepancies": []}
        
        result = await coordinator.run_appraisal({
            "file_path": "tests/conftest.py",
            "institution_id": "DEFAULT"
        })
        
        assert result["status"] == "success"
        assert result["combined_decision"]["decision"] == "APPROVE"
        assert "fallback" in result["combined_decision"]["decision_rationale"].lower()

def test_sqlite_wal_and_busy_timeout_pragmas():
    """Verify that centralized SQLite connection helper activates WAL mode, busy_timeout to 30000ms, and preserves synchronous mode."""
    from app.database.database import get_sqlite_connection
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert str(journal_mode).lower() == "wal"

        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        assert int(busy_timeout) == 30000

        cursor.execute("PRAGMA synchronous;")
        synchronous_mode = cursor.fetchone()[0]
        assert int(synchronous_mode) in [1, 2]  # 1 = NORMAL, 2 = FULL
    finally:
        conn.close()

def test_readyz_health_endpoint(client):
    """Verify GET /readyz returns HTTP 200 OK and status 'ready' using centralized SQLite helper."""
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "online"

def test_sqlite_concurrent_writes():
    """Verify that concurrent thread writes execute successfully under WAL mode without database locking errors."""
    import threading
    from app.database.database import get_sqlite_connection

    errors = []

    def _write_task(task_id):
        try:
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO companies (id, name, sector) VALUES (?, ?, ?)", (f"COMP_THREAD_{task_id}", f"Thread Company {task_id}", "Technology"))
            conn.commit()
            conn.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write_task, args=(i,)) for i in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(errors) == 0, f"Concurrent write errors encountered: {errors}"

def test_uuid_prefixed_upload_filenames_unique(client):
    """Verify that file uploads with identical filenames receive unique UUID-prefixed temporary paths."""
    captured_paths = []
    
    # Dummy minimal valid PDF bytes
    dummy_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"

    async def mock_run_appraisal(self, payload):
        captured_paths.append(payload["file_path"])
        return {
            "status": "success",
            "combined_decision": {"decision": "APPROVE", "decision_rationale": "Mock rationale"}
        }

    with patch("app.routes.documents.AgentCoordinator.run_appraisal", mock_run_appraisal), \
         patch("app.routes.documents.run_pdf_forensics", return_value={"is_suspicious": False, "flags": []}):
        
        files1 = {"file": ("statement.pdf", dummy_pdf, "application/pdf")}
        files2 = {"file": ("statement.pdf", dummy_pdf, "application/pdf")}

        res1 = client.post("/api/v1/documents/ingest/pdf", files=files1)
        res2 = client.post("/api/v1/documents/ingest/pdf", files=files2)

        assert res1.status_code == 200
        assert res2.status_code == 200
        assert len(captured_paths) == 2
        
        path1, path2 = captured_paths[0], captured_paths[1]
        assert path1 != path2
        assert "statement.pdf" in path1
        assert "statement.pdf" in path2
        assert "temp_uploads" in path1
        assert "temp_uploads" in path2

@pytest.mark.asyncio
async def test_startup_cleanup_orphaned_temp_files():
    """Verify that lifespan startup cleanup purges files older than threshold while preserving fresh files."""
    import time
    from app.main import lifespan, app

    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)

    stale_file = os.path.join(temp_dir, "stale_test_file.pdf")
    fresh_file = os.path.join(temp_dir, "fresh_test_file.pdf")

    # Create dummy files
    with open(stale_file, "w") as f:
        f.write("stale content")
    with open(fresh_file, "w") as f:
        f.write("fresh content")

    # Set mtime of stale_file to 2 hours ago (7200s ago)
    two_hours_ago = time.time() - 7200
    os.utime(stale_file, (two_hours_ago, two_hours_ago))

    try:
        # Trigger lifespan startup context
        async with lifespan(app):
            pass

        # Verify stale file was removed and fresh file remains
        assert not os.path.exists(stale_file)
        assert os.path.exists(fresh_file)
    finally:
        if os.path.exists(stale_file):
            os.remove(stale_file)
        if os.path.exists(fresh_file):
            os.remove(fresh_file)

def test_correlation_id_middleware_and_contextvar_lifecycle(client):
    """Verify X-Correlation-ID header generation, request tracing, and ContextVar cleanup."""
    from app.main import correlation_id_ctx

    # Assert ContextVar is None prior to request
    assert correlation_id_ctx.get() is None

    # Request without header
    res1 = client.get("/")
    assert res1.status_code == 200
    assert "X-Correlation-ID" in res1.headers
    cid1 = res1.headers["X-Correlation-ID"]
    assert len(cid1) == 32  # 32-char hex string

    # Assert ContextVar is reset after request
    assert correlation_id_ctx.get() is None

    # Request with valid custom correlation header
    custom_cid = "custom-test-cid-12345"
    res2 = client.get("/", headers={"X-Correlation-ID": custom_cid})
    assert res2.status_code == 200
    assert res2.headers.get("X-Correlation-ID") == custom_cid
    assert correlation_id_ctx.get() is None

def test_correlation_id_malformed_header_rejection(client):
    """Verify malformed client headers (e.g. path traversal) are rejected and replaced with generated UUID."""
    malformed_header = "../../../etc/passwd"
    res = client.get("/", headers={"X-Correlation-ID": malformed_header})
    assert res.status_code == 200
    returned_cid = res.headers.get("X-Correlation-ID")
    assert returned_cid != malformed_header
    assert len(returned_cid) == 32
