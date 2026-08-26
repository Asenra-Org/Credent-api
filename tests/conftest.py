# =============================================================================
# CREDENT — Pytest Test Configuration & Shared Fixtures
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================

import contextlib
import os
import json
import tempfile
import pytest
import ssl
from unittest.mock import patch as mock_patch

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
import uuid
from app.database.database import get_sqlite_connection
from app.security.auth_service import hash_password

from app.security.dependencies import get_current_user_and_session

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Ensure test database is properly initialized prior to test suite execution."""
    init_db()
    yield

@pytest.fixture(autouse=True)
def mock_supabase(monkeypatch):
    """Globally mock Supabase to prevent network calls during tests."""
    monkeypatch.setattr("app.database.database._get_supabase", lambda: None)

# ---------------------------------------------------------------------------
# LLM provider isolation
#
# app.core.llm.ChatGroqWithFallback picks its provider from the environment:
# a configured SARVAM_API_KEY returns a langchain_openai ChatOpenAI pointed at
# Sarvam, otherwise a langchain_groq ResilientChatGroq.
#
# The guard below used to patch ChatGroq only. On a machine with SARVAM_API_KEY
# in .env the agents were ChatOpenAI, so nothing was patched and the suite made
# real network calls to Sarvam - 18 tests failed locally and passed in CI, which
# has no .env. The suite was reporting on the developer's environment rather
# than on the code.
#
# Two changes fix that: the provider is pinned for the test process, and the
# network guard covers every client class the factory can return.
#
# Production precedence is untouched. This affects the test process only.
# ---------------------------------------------------------------------------

TEST_PROVIDER_KEY = "test-key-not-a-real-credential"


@pytest.fixture(autouse=True)
def isolate_llm_providers(monkeypatch):
    """Pin the provider for tests, with a key that cannot reach anything real."""
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", TEST_PROVIDER_KEY)


@pytest.fixture(autouse=True)
def mock_llm_network_calls(monkeypatch):
    """Block real provider calls for every client the factory can return."""
    from unittest.mock import AsyncMock

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    def _blocked(*_a, **_k):
        raise AssertionError(
            "A test attempted a real LLM call. Mock it with patch_agent_llm(agent, ...)."
        )

    for module_name, class_name in (
        ("langchain_groq", "ChatGroq"),
        ("langchain_openai", "ChatOpenAI"),
    ):
        try:
            module = __import__(module_name, fromlist=[class_name])
            client_cls = getattr(module, class_name)
        except Exception:
            continue

        monkeypatch.setattr(client_cls, "ainvoke", AsyncMock(return_value=AIMessage(content="{}")), raising=False)
        agenerate = AsyncMock(
            return_value=LLMResult(generations=[[ChatGeneration(message=AIMessage(content="{}"))]])
        )
        monkeypatch.setattr(client_cls, "agenerate", agenerate, raising=False)
        monkeypatch.setattr(client_cls, "agenerate_prompt", agenerate, raising=False)
        # Synchronous entry points are not used by the agents, but a stray call
        # must fail loudly rather than reach a provider.
        monkeypatch.setattr(client_cls, "invoke", _blocked, raising=False)


@contextlib.contextmanager
def patch_agent_llm(agent, response=None, side_effect=None):
    """Mock an agent's LLM at the CRESEM boundary, whatever provider built it.

    Patching a provider-specific symbol only works while that provider is
    selected. This patches the client object the agent actually holds, so it
    intercepts on the Groq path, the Sarvam path, or any provider added later.

    ``response`` may be a string, which is wrapped in an object exposing
    ``.content`` the way a LangChain message does.
    """
    from unittest.mock import AsyncMock, MagicMock

    if side_effect is not None:
        mock = AsyncMock(side_effect=side_effect)
    else:
        payload = response
        if isinstance(payload, str):
            message = MagicMock()
            message.content = payload
            payload = message
        mock = AsyncMock(return_value=payload)

    # LangChain clients are pydantic models, so an arbitrary attribute cannot be
    # set on the instance. Patch the class the agent's client actually is -
    # resolved at runtime, so this follows the factory to whichever provider it
    # selected rather than naming one.
    with mock_patch.object(type(agent.llm), "ainvoke", mock):
        yield mock

@pytest.fixture
def client():
    """Returns a FastAPI TestClient instance for testing routes."""
    return TestClient(app)

@pytest.fixture
def admin_headers(client):
    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    email = f"admin_{uuid.uuid4()}@example.com"
    password = "password123"
    c.execute("INSERT INTO users (id, email, password_hash, mfa_enabled, is_active, is_locked, failed_login_count) VALUES (?, ?, ?, 0, 1, 0, 0)",
              (user_id, email, hash_password(password)))
    c.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)",
              (user_id, "DEFAULT", "ORG_ADMIN"))
    conn.commit()
    conn.close()

    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    client.cookies.clear()
    token = res.json().get("access_token")
    return {"Authorization": f"Bearer {token}"}

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
