# =============================================================================
# CREDENT — Integration Tests: AgentCoordinator Orchestration Pipeline
# Linear: ASE-36 [QA-W4]
# =============================================================================
"""
Integration tests covering AgentCoordinator.run_appraisal() — the pipeline
that is meant to dispatch a single appraisal request to all five sub-agents
(Ingestion, Financial Health, Promoter/Management Quality, Sector Context,
Integrity Verification), collect their outputs, and compile a single
combined response.

IMPORTANT — current state as of this writing:
    app/agents/orchestration/coordinator.py is still a stub. All three
    methods (run_appraisal, build_evidence_trail, generate_explanation)
    raise NotImplementedError. The real implementation (ASE-32, being built
    by Shlok) has been tested locally per his own report, but has not been
    pushed to any branch/PR in this repo yet.

This file follows the same pattern used for ASE-22 (Promoter agent) when it
was in the same state:
    1. TestCoordinatorCurrentState — confirms today's actual stub behavior
       (these tests PASS right now, documenting the contract honestly).
    2. TestCoordinatorPipelineIntegration — pre-written, marked skip, ready
       to activate the moment run_appraisal() is implemented. Mocks all
       five sub-agents so the coordinator's dispatch logic can be verified
       in isolation, without needing real AI calls or documents.

Run with:
    pytest tests/test_coordinator.py -v
"""

import pytest
from unittest.mock import AsyncMock, patch
from app.agents.orchestration.coordinator import AgentCoordinator


@pytest.fixture
def coordinator() -> AgentCoordinator:
    return AgentCoordinator()


@pytest.fixture
def sample_application_data() -> dict:
    """A minimal, representative application payload for pipeline testing."""
    return {
        "company_name": "Integration Test Co",
        "financial_text": "Revenue: 10,000,000. EBIT: 2,000,000. Total Debt: 5,000,000.",
        "sector": "Manufacturing",
        "promoters": [{"name": "Test Promoter", "experience_years": 10, "risk_flags": []}],
        "gst_data": [{"taxable_value": 100_000, "type": "SALE", "counterparty_gstin": "GSTIN1"}],
        "bank_data": [{"type": "CREDIT", "amount": 100_000}],
    }


# ---------------------------------------------------------------------------
# 1. Current State — AgentCoordinator is still a stub
# ---------------------------------------------------------------------------

class TestCoordinatorCurrentState:
    """
    Documents the coordinator's actual behavior today. These tests PASS
    right now and will need to be removed/updated once ASE-32 lands, since
    the methods will no longer raise NotImplementedError.
    """

    @pytest.mark.asyncio
    async def test_run_appraisal_currently_raises_not_implemented(self, coordinator, sample_application_data):
        with pytest.raises(NotImplementedError):
            await coordinator.run_appraisal(sample_application_data)

    @pytest.mark.asyncio
    async def test_build_evidence_trail_currently_raises_not_implemented(self, coordinator):
        with pytest.raises(NotImplementedError):
            await coordinator.build_evidence_trail({"financial_health": {}})

    @pytest.mark.asyncio
    async def test_generate_explanation_currently_raises_not_implemented(self, coordinator):
        with pytest.raises(NotImplementedError):
            await coordinator.generate_explanation([{"agent": "financial_health", "finding": "test"}])


# ---------------------------------------------------------------------------
# 2. Pipeline Integration — pre-written for when run_appraisal is implemented
# ---------------------------------------------------------------------------
"""
Blocked: AgentCoordinator.run_appraisal() has no orchestration logic yet
(ASE-36, waiting on ASE-32). Un-skip this class once Shlok's implementation
is merged, and adjust patch targets below if the coordinator imports agents
under different names/paths than assumed here.

Each sub-agent's real `analyze()` (or equivalent) method is mocked out, so
these tests verify DISPATCH LOGIC ONLY — i.e. that the coordinator actually
calls each of the five agents during run_appraisal — without needing real
AI calls, a real database, or real documents. This is what makes them fast,
reliable integration tests rather than slow end-to-end tests.
"""

@pytest.mark.skip(reason="Blocked: AgentCoordinator.run_appraisal() not implemented yet (ASE-36, waiting on ASE-32)")
class TestCoordinatorPipelineIntegration:

    @pytest.mark.asyncio
    async def test_run_appraisal_calls_all_five_sub_agents(self, coordinator, sample_application_data):
        """
        Core acceptance criteria for ASE-36: verify that all sub-agents are
        called during run_appraisal — Ingestion, Financial Health, Promoter,
        Sector Context, and Integrity.
        """
        with patch("app.agents.input.document_ingestion.DocumentIngestionAgent.parse_financial_statement", new_callable=AsyncMock) as mock_ingestion, \
             patch("app.agents.analysis.financial_health.FinancialHealthAgent.analyze", new_callable=AsyncMock) as mock_financial, \
             patch("app.agents.analysis.management_quality.ManagementQualityAgent.analyze", new_callable=AsyncMock) as mock_promoter, \
             patch("app.agents.analysis.sector_context.SectorContextAgent.get_sector_outlook", new_callable=AsyncMock) as mock_sector, \
             patch("app.agents.analysis.integrity_verification.IntegrityVerificationAgent.cross_validate", new_callable=AsyncMock) as mock_integrity:

            mock_ingestion.return_value = {"status": "success"}
            mock_financial.return_value = {"status": "success", "financial_health_score": 80.0}
            mock_promoter.return_value = {"status": "success", "management_score": 75.0}
            mock_sector.return_value = {"status": "success", "outlook": "Stable"}
            mock_integrity.return_value = {"status": "completed", "flags_detected": 0}

            await coordinator.run_appraisal(sample_application_data)

            mock_ingestion.assert_called_once()
            mock_financial.assert_called_once()
            mock_promoter.assert_called_once()
            mock_sector.assert_called_once()
            mock_integrity.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_appraisal_returns_compiled_result_with_all_agent_outputs(self, coordinator, sample_application_data):
        """The final result should contain every sub-agent's findings, not just a subset."""
        with patch("app.agents.analysis.financial_health.FinancialHealthAgent.analyze", new_callable=AsyncMock) as mock_financial, \
             patch("app.agents.analysis.management_quality.ManagementQualityAgent.analyze", new_callable=AsyncMock) as mock_promoter, \
             patch("app.agents.analysis.sector_context.SectorContextAgent.get_sector_outlook", new_callable=AsyncMock) as mock_sector, \
             patch("app.agents.analysis.integrity_verification.IntegrityVerificationAgent.cross_validate", new_callable=AsyncMock) as mock_integrity:

            mock_financial.return_value = {"status": "success", "financial_health_score": 80.0}
            mock_promoter.return_value = {"status": "success", "management_score": 75.0}
            mock_sector.return_value = {"status": "success", "outlook": "Stable"}
            mock_integrity.return_value = {"status": "completed", "flags_detected": 0}

            result = await coordinator.run_appraisal(sample_application_data)

            assert "financial_health" in result
            assert "management_quality" in result
            assert "sector_context" in result
            assert "integrity" in result

    @pytest.mark.asyncio
    async def test_one_agent_failure_does_not_crash_entire_pipeline(self, coordinator, sample_application_data):
        """
        Mirrors the real bug Shlok reported (Apple 10-K: AI extraction failed
        with HTTP 413, but the pipeline still returned 200 with fallback
        values instead of crashing). If one sub-agent raises, the coordinator
        should degrade gracefully, not take down the whole appraisal.
        """
        with patch("app.agents.analysis.financial_health.FinancialHealthAgent.analyze", new_callable=AsyncMock) as mock_financial, \
             patch("app.agents.analysis.management_quality.ManagementQualityAgent.analyze", new_callable=AsyncMock) as mock_promoter, \
             patch("app.agents.analysis.sector_context.SectorContextAgent.get_sector_outlook", new_callable=AsyncMock) as mock_sector, \
             patch("app.agents.analysis.integrity_verification.IntegrityVerificationAgent.cross_validate", new_callable=AsyncMock) as mock_integrity:

            mock_financial.side_effect = Exception("Simulated LLM token limit exceeded (HTTP 413)")
            mock_promoter.return_value = {"status": "success", "management_score": 75.0}
            mock_sector.return_value = {"status": "success", "outlook": "Stable"}
            mock_integrity.return_value = {"status": "completed", "flags_detected": 0}

            result = await coordinator.run_appraisal(sample_application_data)

            assert result is not None
            assert result.get("status") != "crashed"

    @pytest.mark.asyncio
    async def test_run_appraisal_persists_result_to_database(self, coordinator, sample_application_data):
        """Confirms the pipeline actually saves its output, mirroring the ASE-29
        lesson learned: a feature can work in isolation but never reach persistence."""
        with patch("app.database.database.save_appraisal") as mock_save, \
             patch("app.agents.analysis.financial_health.FinancialHealthAgent.analyze", new_callable=AsyncMock) as mock_financial, \
             patch("app.agents.analysis.management_quality.ManagementQualityAgent.analyze", new_callable=AsyncMock) as mock_promoter, \
             patch("app.agents.analysis.sector_context.SectorContextAgent.get_sector_outlook", new_callable=AsyncMock) as mock_sector, \
             patch("app.agents.analysis.integrity_verification.IntegrityVerificationAgent.cross_validate", new_callable=AsyncMock) as mock_integrity:

            mock_financial.return_value = {"status": "success", "financial_health_score": 80.0}
            mock_promoter.return_value = {"status": "success", "management_score": 75.0}
            mock_sector.return_value = {"status": "success", "outlook": "Stable"}
            mock_integrity.return_value = {"status": "completed", "flags_detected": 0}
            mock_save.return_value = "REC_TEST123"

            await coordinator.run_appraisal(sample_application_data)

            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_evidence_trail_includes_entry_per_agent(self, coordinator):
        """Evidence trail should have one traceable entry per sub-agent finding."""
        agent_outputs = {
            "financial_health": {"financial_health_score": 80.0},
            "management_quality": {"management_score": 75.0},
            "sector_context": {"outlook": "Stable"},
            "integrity": {"flags_detected": 0},
        }
        trail = await coordinator.build_evidence_trail(agent_outputs)

        assert isinstance(trail, list)
        assert len(trail) >= len(agent_outputs)
