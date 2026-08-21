import pytest
import os
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_semantic_hallucination_gap():
    """
    Demonstrates the explicit semantic validation gap.
    If the LLM hallucinates 50 Cr debt instead of 500 Cr,
    FinancialHealthAgent cannot detect it because it does not receive the raw PDF text.
    """
    from app.agents.analysis.financial_health import FinancialHealthAgent

    agent = FinancialHealthAgent()

    extracted_financials = {
        "total_debt": 50.0,  # LLM extracted 50, but source said 500
        "total_equity": 100.0,
        "net_operating_income": 20.0,
        "cash_flow_available_for_debt_service": 20.0
    }

    # Analyze only receives the extracted data, not the source text.
    # Therefore, it cannot know that 50 is wrong.
    result = await agent.analyze(extracted_financials)

    # The output builds ratios on the hallucinated 50.0 debt without source verification.
    assert result["status"] == "success"
    assert result["ratios"]["debt_to_equity"] == 0.5  # 50 / 100

    # The key point: there is no source validation step here.
    assert "source_text" not in extracted_financials
