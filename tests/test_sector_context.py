import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.agents.analysis.sector_context import SectorContextAgent

@pytest.fixture
def agent():
    with patch("app.agents.analysis.sector_context.ChatGroq"):
        return SectorContextAgent()

@pytest.mark.asyncio
async def test_get_sector_outlook_empty_input(agent):
    result = await agent.get_sector_outlook("")
    assert result["sector"] == "Unknown Sector"
    assert result["outlook"] == "Stable"

@pytest.mark.asyncio
async def test_get_sector_outlook_mocked_success(agent):
    # Just mock ainvoke on a custom object
    class MockChain:
        async def ainvoke(self, params):
            class MockOutput:
                def model_dump(self):
                    return {
                        "outlook": "Negative",
                        "growth_rate_projected": "3.5%",
                        "risk_score": 8,
                        "risk_factors": ["High inflation", "Supply chain issues"]
                    }
            return MockOutput()
            
    with patch("app.agents.analysis.sector_context.ChatPromptTemplate.__or__", return_value=MockChain()):
        result = await agent.get_sector_outlook("Manufacturing")
        assert result["sector"] == "Manufacturing"
        assert result["outlook"] == "Negative"
        assert result["risk_score"] == 8
        assert result["risk_level"] == "High"
        assert "High inflation" in result["risk_factors"]

@pytest.mark.asyncio
async def test_check_rbi_policies_empty_input(agent):
    result = await agent.check_rbi_policies("   ")
    assert len(result) == 1
    assert result[0]["circular_ref"] == "N/A"

@pytest.mark.asyncio
async def test_check_rbi_policies_mocked_success(agent):
    class MockChain:
        async def ainvoke(self, params):
            class MockOutput:
                def model_dump(self):
                    return {
                        "policies": [
                            {
                                "circular_ref": "RBI/2023-24/101",
                                "summary": "Tightening unsecured loans",
                                "impact": "Unfavorable"
                            }
                        ]
                    }
            return MockOutput()
            
    with patch("app.agents.analysis.sector_context.ChatPromptTemplate.__or__", return_value=MockChain()):
        result = await agent.check_rbi_policies("Banking")
        assert len(result) == 1
        assert result[0]["circular_ref"] == "RBI/2023-24/101"
        assert result[0]["impact"] == "Unfavorable"

def test_risk_level_logic():
    assert SectorContextAgent._risk_level_from_score(2) == "Low"
    assert SectorContextAgent._risk_level_from_score(5) == "Medium"
    assert SectorContextAgent._risk_level_from_score(9) == "High"
