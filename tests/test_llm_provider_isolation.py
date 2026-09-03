"""Tests must behave the same whichever provider a developer has configured.

The bug this pins down: ``app.core.llm.ChatGroqWithFallback`` picks its provider
from the environment. A configured SARVAM_API_KEY returns a langchain_openai
``ChatOpenAI`` pointed at Sarvam; otherwise a langchain_groq
``ResilientChatGroq``. Several tests patched
``langchain_groq.ChatGroq.ainvoke``, which only exists on the second path.

So a developer with SARVAM_API_KEY in .env ran a suite whose mocks silently did
not intercept - the tests made real network calls to Sarvam and 18 of them
failed. CI, which has no .env, took the Groq path and passed. The suite was
reporting on the developer's environment rather than on the code.

Production provider precedence is deliberately NOT changed by any of this.
"""

import os

import pytest

from app.core.llm import active_provider


# ---------------------------------------------------------------------------
# The environment tests actually run in
# ---------------------------------------------------------------------------

class TestProviderEnvironmentIsPinned:
    def test_sarvam_is_removed_for_tests(self):
        """The autouse fixture in conftest.py removes it from the test process."""
        assert os.getenv("SARVAM_API_KEY") is None

    def test_groq_key_is_a_fake(self):
        """A test must not be able to reach a real provider even unmocked."""
        key = os.getenv("GROQ_API_KEY")
        assert key == "test-key-not-a-real-credential"
        assert not key.startswith("gsk_")

    def test_the_factory_is_deterministic_under_test(self):
        """Same branch for every developer, matching CI."""
        assert active_provider()["provider"] == "groq"


# ---------------------------------------------------------------------------
# The regression: isolation must hold even when Sarvam IS configured
# ---------------------------------------------------------------------------

class TestIsolationHoldsWhenSarvamIsPresent:
    def test_factory_still_prefers_sarvam_in_production_config(self, monkeypatch):
        """Precedence is unchanged - this is a test-harness fix, not a product change."""
        monkeypatch.setenv("SARVAM_API_KEY", "sarvam-test-value")
        resolved = active_provider()
        assert resolved["provider"] == "sarvam"
        assert resolved["primary_model"] == "sarvam-105b"

    @pytest.mark.asyncio
    async def test_mock_intercepts_on_the_sarvam_path(self, monkeypatch):
        """The actual regression.

        With SARVAM_API_KEY set the agent's client is a ChatOpenAI, so a patch
        of langchain_groq.ChatGroq.ainvoke would not intercept. The helper
        patches the client the agent holds, so it intercepts either way and no
        network call is made.
        """
        from conftest import patch_agent_llm
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        monkeypatch.setenv("SARVAM_API_KEY", "sarvam-test-value")
        agent = DocumentIngestionAgent()

        # Confirm we really are on the Sarvam path for this test.
        assert type(agent.llm).__name__ == "SarvamChatWrapper"

        agent.structured_llm = None
        payload = """
        {
            "company_name": "Isolation Test Ltd", "sector": "Manufacturing",
            "ebitda": null, "pat": null, "total_revenue": 100, "total_debt": null,
            "shareholder_equity": null, "current_assets": null, "current_liabilities": null,
            "base_score": 70, "qualitative_notes": null,
            "financial_commitments": [], "legal_risks": [], "sanction_details": [],
            "citations": {"revenue": null, "debt": null, "equity": null}
        }
        """
        with patch_agent_llm(agent, response=payload) as mock:
            result = await agent.parse_financial_statement("revenue balance sheet")

        assert mock.await_count >= 1, "the mock must have intercepted the call"
        assert result["company_name"] == "Isolation Test Ltd"

    @pytest.mark.asyncio
    async def test_mock_intercepts_on_the_groq_path(self, monkeypatch):
        """The same helper works on the default path."""
        from conftest import patch_agent_llm
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        monkeypatch.delenv("SARVAM_API_KEY", raising=False)
        agent = DocumentIngestionAgent()
        assert type(agent.llm).__name__ == "ResilientChatGroq"

        agent.structured_llm = None
        # The same document text the Sarvam case uses. Ingestion short-circuits
        # on input that carries no financial keywords, which would skip the LLM
        # call entirely and make this assertion meaningless.
        payload = """
        {
            "company_name": "Groq Path Ltd", "sector": "Manufacturing",
            "ebitda": null, "pat": null, "total_revenue": 100, "total_debt": null,
            "shareholder_equity": null, "current_assets": null, "current_liabilities": null,
            "base_score": 70, "qualitative_notes": null,
            "financial_commitments": [], "legal_risks": [], "sanction_details": [],
            "citations": {"revenue": null, "debt": null, "equity": null}
        }
        """
        with patch_agent_llm(agent, response=payload) as mock:
            result = await agent.parse_financial_statement("revenue balance sheet")

        assert mock.await_count >= 1, "the mock must have intercepted the call"
        assert result["company_name"] == "Groq Path Ltd"


# ---------------------------------------------------------------------------
# No test reaches a real provider
# ---------------------------------------------------------------------------

class TestNoRealProviderCalls:
    def test_no_test_file_patches_a_provider_specific_symbol(self):
        """Provider-specific patch targets are what caused the divergence.

        Mocking belongs at the agent's client boundary, which is stable across
        providers.
        """
        import pathlib

        tests_dir = pathlib.Path(__file__).resolve().parent
        offenders = []
        for path in tests_dir.glob("test_*.py"):
            if path.name == pathlib.Path(__file__).name:
                continue   # this file names the pattern in order to forbid it
            text = path.read_text(encoding="utf-8", errors="replace")
            for target in ('patch("langchain_groq', "patch('langchain_groq",
                           'patch("langchain_openai', "patch('langchain_openai"):
                if target in text:
                    offenders.append(path.name)
                    break
        assert offenders == [], (
            f"these tests patch a provider-specific symbol and will not intercept "
            f"when another provider is configured: {offenders}"
        )

    def test_helper_is_available_to_every_test(self):
        from conftest import patch_agent_llm

        assert callable(patch_agent_llm)
