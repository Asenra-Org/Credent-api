# =============================================================================
# CREDENT — Pytest Test Configuration & Shared Fixtures
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import os
import json
import tempfile
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database.database import init_db, DB_PATH

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Ensure test database is properly initialized prior to test suite execution."""
    init_db()
    # Add a default tenant for integration tests relying on X-Tenant-ID
    try:
        from app.database.session import get_session_factory
        from app.models.ase52 import Tenant
        with get_session_factory()() as session:
            if not session.query(Tenant).filter_by(id="test_tenant").first():
                session.add(Tenant(id="test_tenant", name="Test Tenant"))
                session.commit()
    except Exception:
        pass
    yield

@pytest.fixture
def client():
    """Returns a FastAPI TestClient instance for testing routes."""
    return TestClient(app)

@pytest.fixture
def sample_policy_payload():
    """Returns a valid institutional policy configuration payload."""
    return {
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

@pytest.fixture
def dummy_pdf_file():
    """Generates a temporary dummy PDF file for testing file upload routes."""
    temp_dir = tempfile.mkdtemp()
    file_path = os.path.join(temp_dir, "test_statement.pdf")
    
    # Write a simple valid PDF header & minimal content
    with open(file_path, "wb") as f:
        f.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
        f.write(b"2 0 obj\n<< /Type /Pages /Kinds [] /Count 0 >>\nendobj\n")
        f.write(b"xref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n")
        f.write(b"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n109\n%%EOF\n")
        
    yield file_path
    
    # Cleanup after test execution
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass
