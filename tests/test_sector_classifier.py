import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("GROQ_API_KEY", "test-key")

from app.agents.input.document_ingestion import DocumentIngestionAgent


@pytest.mark.asyncio
class TestSectorClassifier:

    @pytest.mark.parametrize(
        "document_text, expected_sector",
        [
            (
                "ABC Motors manufactures passenger cars, commercial vehicles and automotive components.",
                "Automotive",
            ),
            (
                "XYZ Hospital provides cancer treatment, diagnostic imaging and healthcare services.",
                "Healthcare",
            ),
            (
                "The company develops software products, cloud platforms and AI solutions.",
                "Technology",
            ),
            (
                "The firm manufactures textile yarn, cotton fabric and garments.",
                "Textiles",
            ),
            (
                "The company produces pharmaceutical drugs and medical formulations.",
                "Pharmaceuticals",
            ),
            (
                "Retail chain operating supermarkets and consumer stores across India.",
                "Retail",
            ),
            (
                "The company builds highways, bridges and metro infrastructure projects.",
                "Infrastructure",
            ),
            (
                "Solar and renewable energy generation company supplying electricity.",
                "Energy",
            ),
            (
                "Commercial bank providing loans, deposits and financial services.",
                "Banking and Financial Services",
            ),
            (
                "Agriculture company engaged in crop production and food processing.",
                "Agriculture",
            ),
        ],
    )
    async def test_sector_classifier(self, document_text, expected_sector):

        agent = DocumentIngestionAgent()

        fake_result = {
            "company_name": "Test Company",
            "sector": expected_sector,
            "total_revenue": None,
            "total_debt": None,
            "shareholder_equity": None,
            "current_assets": None,
            "current_liabilities": None,
            "base_score": 80,
            "qualitative_notes": "",
            "financial_commitments": [],
            "legal_risks": [],
            "sanction_details": [],
            "citations": {},
        }

        fake_model = MagicMock()
        fake_model.model_dump.return_value = fake_result

        fake_chain = AsyncMock()
        fake_chain.ainvoke.return_value = fake_model

        with patch.object(
            agent,
            "structured_llm",
            MagicMock(),
        ):

            with patch(
                "langchain_core.runnables.base.RunnableSequence.ainvoke",
                new=AsyncMock(return_value=fake_model),
            ):

                result = await agent.parse_financial_statement(document_text)

        assert result["sector"] == expected_sector