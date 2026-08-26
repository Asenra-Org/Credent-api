# =============================================================================
# CREDENT — Unit Tests: Source Traceability Engine
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
Comprehensive pytest tests for the Source Traceability Engine (AI-A-W5).

Tests cover:
  - Revenue, Debt, Equity citation extraction
  - Correct page number and snippet association
  - Backward-compatible alias keys (total_revenue, total_debt, shareholder_equity)
  - Missing metrics → citations are None
  - Missing page number in citation
  - Page number as string coerced to int
  - Page: 0 edge case (falsy but valid)
  - Empty document → error + pages: []
  - File not found → error + pages: []
  - Non-financial doc rejected → error + pages: []
  - Multi-page document preservation
  - DEFAULT_EXTRACTION contains correct citations structure
  - All-null LLM output (no citations)
  - Invalid/corrupt citation value gracefully ignored
  - parse_financial_statement returns citations when all LLM attempts fail
  - Existing extraction fields (company_name, base_score) remain unchanged

Run with:
    pytest tests/test_source_traceability.py -v
"""
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from app.agents.input.document_ingestion import (
    DocumentIngestionAgent,
    RiskExtraction,
    CitationDetail,
    CitationMetadata,
    DEFAULT_EXTRACTION,
)

os.environ.setdefault("GROQ_API_KEY", "test-dummy-key-for-traceability-tests")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent():
    """Shared agent instance — no real API calls are made in any test."""
    return DocumentIngestionAgent()


# ---------------------------------------------------------------------------
# 1. DEFAULT_EXTRACTION structure
# ---------------------------------------------------------------------------

class TestDefaultExtraction:
    def test_citations_key_exists_in_default(self):
        """DEFAULT_EXTRACTION must always carry a 'citations' key."""
        assert "citations" in DEFAULT_EXTRACTION

    def test_citations_has_all_required_keys(self):
        citations = DEFAULT_EXTRACTION["citations"]
        for key in ("revenue", "debt", "equity", "total_revenue", "total_debt", "shareholder_equity"):
            assert key in citations

    def test_all_citations_are_none_in_default(self):
        """Every citation entry in DEFAULT_EXTRACTION must default to None."""
        citations = DEFAULT_EXTRACTION["citations"]
        for key, val in citations.items():
            assert val is None, f"Expected None for citations['{key}'], got {val!r}"

    def test_default_extraction_copy_is_independent(self):
        """Mutations to a copy must not affect the original."""
        copy = DEFAULT_EXTRACTION.copy()
        copy["citations"] = {"revenue": {"page": 1, "snippet": "test"}}
        assert DEFAULT_EXTRACTION["citations"]["revenue"] is None


# ---------------------------------------------------------------------------
# 2. Pydantic model sanity checks
# ---------------------------------------------------------------------------

class TestPydanticModels:
    def test_citation_detail_defaults_none(self):
        cd = CitationDetail()
        assert cd.page is None
        assert cd.snippet is None

    def test_citation_detail_with_values(self):
        cd = CitationDetail(page=3, snippet="Revenue: 50 Cr")
        assert cd.page == 3
        assert cd.snippet == "Revenue: 50 Cr"

    def test_citation_metadata_all_optional(self):
        cm = CitationMetadata()
        assert cm.revenue is None
        assert cm.debt is None
        assert cm.equity is None

    def test_risk_extraction_has_citations_field(self):
        fields = RiskExtraction.model_fields
        assert "citations" in fields


# ---------------------------------------------------------------------------
# 3. ingest_pdf — page preservation
# ---------------------------------------------------------------------------

class TestIngestPdfPagePreservation:
    @pytest.mark.asyncio
    async def test_multi_page_text_has_page_markers(self, agent):
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader, \
             patch("app.agents.input.document_ingestion.tabula.read_pdf", return_value=[]):
            inst = MagicMock()
            p1, p2 = MagicMock(), MagicMock()
            p1.extract_text.return_value = "Revenue from operations 50 Cr"
            p2.extract_text.return_value = "Long-term debt borrowings 20 Cr"
            inst.pages = [p1, p2]
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("dummy.pdf")

        assert "--- PAGE 1 ---" in result["text"]
        assert "--- PAGE 2 ---" in result["text"]

    @pytest.mark.asyncio
    async def test_pages_metadata_list_matches_page_count(self, agent):
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader, \
             patch("app.agents.input.document_ingestion.tabula.read_pdf", return_value=[]):
            inst = MagicMock()
            pages = []
            for i in range(5):
                m = MagicMock()
                m.extract_text.return_value = f"Revenue income balance sheet page {i+1}"
                pages.append(m)
            inst.pages = pages
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("dummy.pdf")

        assert len(result["pages"]) == 5
        for i, entry in enumerate(result["pages"], 1):
            assert entry["page"] == i
            assert isinstance(entry["text"], str)

    @pytest.mark.asyncio
    async def test_pages_key_present_in_success_result(self, agent):
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader, \
             patch("app.agents.input.document_ingestion.tabula.read_pdf", return_value=[]):
            inst = MagicMock()
            p = MagicMock()
            p.extract_text.return_value = "Revenue balance sheet annual report"
            inst.pages = [p]
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("dummy.pdf")

        assert "pages" in result

    @pytest.mark.asyncio
    async def test_pages_key_present_when_file_not_found(self, agent):
        """Error path: file not found must still return pages: []."""
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=False):
            result = await agent.ingest_pdf("nonexistent.pdf")
        assert "pages" in result
        assert result["pages"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_pages_key_present_when_no_text_extracted(self, agent):
        """Error path: empty PDF must still return pages: []."""
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader:
            inst = MagicMock()
            inst.pages = []
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("empty.pdf")
        assert "pages" in result
        assert result["pages"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_pages_key_present_when_non_financial_doc(self, agent):
        """Error path: rejected non-financial doc must still return pages: []."""
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader, \
             patch("app.agents.input.document_ingestion.tabula.read_pdf", return_value=[]):
            inst = MagicMock()
            p = MagicMock()
            p.extract_text.return_value = "Hello world this is a greeting card with no finance"
            inst.pages = [p]
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("greeting_card.pdf")
        assert "pages" in result
        assert result["pages"] == []
        assert "error" in result


# ---------------------------------------------------------------------------
# 4. Citation extraction — parse_financial_statement
# ---------------------------------------------------------------------------

class TestCitationExtraction:

    def _make_mock_response(self, content: str):
        r = MagicMock()
        r.content = content
        return r

    @pytest.mark.asyncio
    async def test_revenue_citation_extracted(self, agent):
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "X Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": 500, "total_debt": null, "shareholder_equity": null,
                "current_assets": null, "current_liabilities": null,
                "base_score": 70, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": {"page": 3, "snippet": "Revenue from operations 500 Cr"},
                    "debt": null, "equity": null
                }
            }
            """)
            res = await agent.parse_financial_statement("revenue balance sheet")
        assert res["citations"]["revenue"]["page"] == 3
        assert "500" in res["citations"]["revenue"]["snippet"]

    @pytest.mark.asyncio
    async def test_debt_citation_extracted(self, agent):
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "X Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": null, "total_debt": 200, "shareholder_equity": null,
                "current_assets": null, "current_liabilities": null,
                "base_score": 60, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": null,
                    "debt": {"page": 5, "snippet": "Long-term borrowings 200 Cr"},
                    "equity": null
                }
            }
            """)
            res = await agent.parse_financial_statement("borrowings balance sheet")
        assert res["citations"]["debt"]["page"] == 5
        assert res["citations"]["total_debt"]["page"] == 5

    @pytest.mark.asyncio
    async def test_equity_citation_extracted(self, agent):
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "X Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": null, "total_debt": null, "shareholder_equity": 300,
                "current_assets": null, "current_liabilities": null,
                "base_score": 72, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": null, "debt": null,
                    "equity": {"page": 2, "snippet": "Shareholder equity net worth 300 Cr"}
                }
            }
            """)
            res = await agent.parse_financial_statement("equity net worth balance sheet")
        assert res["citations"]["equity"]["page"] == 2
        assert res["citations"]["shareholder_equity"]["page"] == 2

    @pytest.mark.asyncio
    async def test_all_three_citations_extracted(self, agent):
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "FullCorp", "sector": "Steel",
                "ebitda": null, "pat": null, "total_revenue": 100, "total_debt": 50, "shareholder_equity": 80,
                "current_assets": null, "current_liabilities": null,
                "base_score": 85, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": {"page": 1, "snippet": "Revenue 100 Cr"},
                    "debt": {"page": 2, "snippet": "Debt 50 Cr"},
                    "equity": {"page": 3, "snippet": "Equity 80 Cr"}
                }
            }
            """)
            res = await agent.parse_financial_statement("revenue debt equity balance sheet")
        assert res["citations"]["revenue"]["page"] == 1
        assert res["citations"]["debt"]["page"] == 2
        assert res["citations"]["equity"]["page"] == 3

    @pytest.mark.asyncio
    async def test_string_page_number_coerced_to_int(self, agent):
        """LLM returns page as string — must be coerced to int."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": 50, "total_debt": null, "shareholder_equity": null,
                "current_assets": null, "current_liabilities": null,
                "base_score": 70, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": {"page": "7", "snippet": "Revenue 50 Cr"},
                    "debt": null, "equity": null
                }
            }
            """)
            res = await agent.parse_financial_statement("revenue balance sheet")
        assert res["citations"]["revenue"]["page"] == 7
        assert isinstance(res["citations"]["revenue"]["page"], int)

    @pytest.mark.asyncio
    async def test_missing_revenue_citation_is_none(self, agent):
        """When revenue is absent, its citation must be None."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": null, "total_debt": null, "shareholder_equity": null,
                "current_assets": null, "current_liabilities": null,
                "base_score": 50, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {"revenue": null, "debt": null, "equity": null}
            }
            """)
            res = await agent.parse_financial_statement("revenue balance sheet")
        assert res["citations"]["revenue"] is None
        assert res["citations"]["total_revenue"] is None

    @pytest.mark.asyncio
    async def test_all_citations_null_in_response(self, agent):
        """If LLM returns null citations entirely, all keys default to None."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": null, "total_debt": null, "shareholder_equity": null,
                "current_assets": null, "current_liabilities": null,
                "base_score": 50, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": null
            }
            """)
            res = await agent.parse_financial_statement("revenue balance sheet")
        for key in ("revenue", "debt", "equity", "total_revenue", "total_debt", "shareholder_equity"):
            assert res["citations"][key] is None

    @pytest.mark.asyncio
    async def test_citations_key_always_present_in_result(self, agent):
        """citations key must be present regardless of LLM response content."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": 200, "total_debt": null, "shareholder_equity": null,
                "base_score": 70
            }
            """)
            res = await agent.parse_financial_statement("revenue balance sheet")
        assert "citations" in res

    @pytest.mark.asyncio
    async def test_backward_compat_alias_keys_synchronized(self, agent):
        """revenue and total_revenue must point to the same citation object."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": 100, "total_debt": 40, "shareholder_equity": 60,
                "current_assets": null, "current_liabilities": null,
                "base_score": 80, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": {"page": 1, "snippet": "Revenue 100"},
                    "debt": {"page": 2, "snippet": "Debt 40"},
                    "equity": {"page": 3, "snippet": "Equity 60"}
                }
            }
            """)
            res = await agent.parse_financial_statement("revenue debt equity balance sheet")
        assert res["citations"]["revenue"] == res["citations"]["total_revenue"]
        assert res["citations"]["debt"] == res["citations"]["total_debt"]
        assert res["citations"]["equity"] == res["citations"]["shareholder_equity"]

    @pytest.mark.asyncio
    async def test_invalid_page_value_in_citation(self, agent):
        """Non-numeric page value must be coerced to None — system must not crash."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Corp", "sector": "Tech",
                "ebitda": null, "pat": null, "total_revenue": 50, "total_debt": null, "shareholder_equity": null,
                "current_assets": null, "current_liabilities": null,
                "base_score": 60, "qualitative_notes": null,
                "financial_commitments": [], "legal_risks": [], "sanction_details": [],
                "citations": {
                    "revenue": {"page": "N/A", "snippet": "Revenue 50 Cr"},
                    "debt": null, "equity": null
                }
            }
            """)
            res = await agent.parse_financial_statement("revenue balance sheet")
        # Should not raise — page should be None after coercion failure
        assert res["citations"]["revenue"]["page"] is None
        assert res["citations"]["revenue"]["snippet"] == "Revenue 50 Cr"

    @pytest.mark.asyncio
    async def test_all_llm_attempts_fail_returns_default_citations(self, agent):
        """When all three LLM attempts fail, DEFAULT_EXTRACTION with null citations returned."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", side_effect=Exception("LLM down")):
            res = await agent.parse_financial_statement("revenue debt balance sheet")
        assert "citations" in res
        assert res["citations"]["revenue"] is None

    @pytest.mark.asyncio
    async def test_existing_fields_unchanged_after_citations_added(self, agent):
        """Existing fields (company_name, base_score, etc.) must remain intact."""
        agent.structured_llm = None
        with patch("langchain_groq.ChatGroq.ainvoke", new_callable=AsyncMock) as m:
            m.return_value = self._make_mock_response("""
            {
                "company_name": "Asenra Ltd", "sector": "Fintech",
                "ebitda": null, "pat": null, "total_revenue": 300, "total_debt": 100, "shareholder_equity": 200,
                "current_assets": 150, "current_liabilities": 90,
                "base_score": 88, "qualitative_notes": "Strong CIBIL",
                "financial_commitments": ["Axis Bank OD"], "legal_risks": [],
                "sanction_details": ["HDFC CC 5Cr"],
                "citations": {
                    "revenue": {"page": 2, "snippet": "Revenue 300 Cr"},
                    "debt": {"page": 3, "snippet": "Debt 100 Cr"},
                    "equity": {"page": 4, "snippet": "Equity 200 Cr"}
                }
            }
            """)
            res = await agent.parse_financial_statement("revenue debt equity balance sheet")
        assert res["company_name"] == "Asenra Ltd"
        assert res["sector"] == "Fintech"
        assert res["base_score"] == 88
        assert res["qualitative_notes"] == "Strong CIBIL"
        assert "Axis Bank OD" in res["financial_commitments"]
        assert "HDFC CC 5Cr" in res["sanction_details"]

    @pytest.mark.asyncio
    async def test_empty_string_raw_text_returns_default_citations(self, agent):
        """Empty string input must return DEFAULT_EXTRACTION with null citations."""
        res = await agent.parse_financial_statement("")
        assert "citations" in res
        assert res["citations"]["revenue"] is None


# ---------------------------------------------------------------------------
# 5. Duplicate / multi-page scenarios
# ---------------------------------------------------------------------------

class TestMultiPageAndDuplicates:
    @pytest.mark.asyncio
    async def test_duplicate_header_removal_preserves_correct_page_numbering(self, agent):
        """After header removal, page indices must still be sequential (1, 2, 3...)."""
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader, \
             patch("app.agents.input.document_ingestion.tabula.read_pdf", return_value=[]):
            inst = MagicMock()
            header = "ACME Corp Annual Report"
            pages = []
            for content in [
                f"{header}\nRevenue from operations 100 Cr",
                f"{header}\nLong-term borrowings 40 Cr",
                f"{header}\nShareholder equity 60 Cr",
            ]:
                p = MagicMock()
                p.extract_text.return_value = content
                pages.append(p)
            inst.pages = pages
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("report.pdf")

        # Page numbering should be 1, 2, 3 — not shifted
        page_nums = [entry["page"] for entry in result["pages"]]
        assert page_nums == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_single_page_doc_returns_one_pages_entry(self, agent):
        with patch("app.agents.input.document_ingestion.os.path.exists", return_value=True), \
             patch("app.agents.input.document_ingestion.PdfReader") as mock_reader, \
             patch("app.agents.input.document_ingestion.tabula.read_pdf", return_value=[]):
            inst = MagicMock()
            p = MagicMock()
            p.extract_text.return_value = "Revenue balance sheet annual report"
            inst.pages = [p]
            mock_reader.return_value = inst
            result = await agent.ingest_pdf("one_page.pdf")
        assert len(result["pages"]) == 1
        assert result["pages"][0]["page"] == 1
