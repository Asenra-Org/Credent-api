# =============================================================================
# CREDENT — Pytest Test Configuration & Shared Fixtures
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import os
import json
import tempfile
import pytest
import ssl

# Cache SSL context to prevent Windows deadlock during thousands of httpx.AsyncClient instantiations
_cached_ssl_context = None
_original_create_default_context = ssl.create_default_context

def _cached_create_default_context(*args, **kwargs):
    global _cached_ssl_context
    if _cached_ssl_context is None:
        _cached_ssl_context = _original_create_default_context(*args, **kwargs)
    return _cached_ssl_context

ssl.create_default_context = _cached_create_default_context

os.environ.setdefault('GROQ_API_KEY', 'dummy-key-for-tests')

from fastapi.testclient import TestClient
from app.main import app
from app.database.database import init_db, DB_PATH

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Ensure test database is properly initialized prior to test suite execution."""
    init_db()
    yield

@pytest.fixture(autouse=True)
def mock_supabase(monkeypatch):
    """Globally mock Supabase to prevent network calls during tests."""
    monkeypatch.setattr("app.database.database._get_supabase", lambda: None)

@pytest.fixture(autouse=True)
def mock_groq_network_calls(monkeypatch):
    """Globally mock Groq to prevent real API calls and deadlocks during tests."""
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import AIMessage
        from unittest.mock import AsyncMock
        
        # This prevents normal ainvoke calls from hitting the network
        mock_ainvoke = AsyncMock(return_value=AIMessage(content="{}"))
        monkeypatch.setattr(ChatGroq, "ainvoke", mock_ainvoke)
        
        # This prevents with_structured_output from hitting the network
        mock_agenerate = AsyncMock()
        from langchain_core.outputs import LLMResult, ChatGeneration
        mock_agenerate.return_value = LLMResult(generations=[[ChatGeneration(message=AIMessage(content='{}'))]])
        monkeypatch.setattr(ChatGroq, "agenerate", mock_agenerate)
        monkeypatch.setattr(ChatGroq, "agenerate_prompt", mock_agenerate)
    except ImportError:
        pass

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
