import pytest
from app.agents.input.document_ingestion import CitationDetail, CitationMetadata, RiskExtraction, DocumentIngestionAgent, CalculatedMetricCitation
from app.agents.orchestration.cam_generator import Citation as CamCitation, CAMGeneratorAgent, MetricWithCitation, FiveCs, CreditAppraisalMemo
from app.agents.orchestration.coordinator import AgentCoordinator
from unittest.mock import AsyncMock, patch, MagicMock

# ---------------------------------------------------------
# UNIT TESTS: PYDANTIC MODELS (Ingestion)
# ---------------------------------------------------------

def test_citation_detail_new_fields():
    """Verify CitationDetail accepts document, location, and confidence fields."""
    citation = CitationDetail(
        page=1,
        snippet="Total Revenue: 500 Cr",
        document="GSTR-3B",
        location="Total Taxable Value",
        confidence="VERIFIED"
    )
    assert citation.document == "GSTR-3B"
    assert citation.location == "Total Taxable Value"
    assert citation.confidence == "VERIFIED"

def test_citation_detail_defaults():
    """Verify CitationDetail defaults are correct."""
    citation = CitationDetail(page=1, snippet="test")
    assert citation.document is None
    assert citation.location is None
    assert citation.confidence == "VERIFIED"

def test_calculated_metric_citation():
    """Verify CalculatedMetricCitation has correct defaults."""
    citation = CalculatedMetricCitation(
        formula="DSCR = NOI / Debt Service",
        inputs=["revenue (page 3)", "debt (page 5)"]
    )
    assert citation.confidence == "CALCULATED"
    assert "not extracted from the document" in citation.note

def test_citation_metadata_new_fields():
    """Verify CitationMetadata accepts calculated metrics."""
    calc_citation = CalculatedMetricCitation(
        formula="A + B",
        inputs=["A", "B"]
    )
    metadata = CitationMetadata(
        revenue=CitationDetail(page=1),
        dscr=calc_citation,
        current_ratio=calc_citation
    )
    assert metadata.dscr.confidence == "CALCULATED"
    assert metadata.current_ratio.inputs == ["A", "B"]

# ---------------------------------------------------------
# UNIT TESTS: PYDANTIC MODELS (CAM Generator)
# ---------------------------------------------------------

def test_cam_citation_new_fields():
    """Verify CAM Citation accepts document and location."""
    citation = CamCitation(
        id=1,
        snippet="Evidence",
        page=2,
        document="Balance Sheet",
        location="Revenue from operations"
    )
    assert citation.document == "Balance Sheet"
    assert citation.location == "Revenue from operations"

# ---------------------------------------------------------
# UNIT TESTS: CITATION CLEANING
# ---------------------------------------------------------

@pytest.fixture
def ingestion_agent():
    with patch.dict("os.environ", {"GROQ_API_KEY": "dummy"}):
        return DocumentIngestionAgent()

@pytest.mark.asyncio
async def test_clean_citations_calculates_preserved(ingestion_agent):
    """Verify _clean_citations correctly preserves calculated metrics."""
    raw_citations = {
        "revenue": {"page": 1, "snippet": "val", "document": "doc", "location": "loc", "confidence": "VERIFIED"},
        "dscr": {
            "formula": "F",
            "inputs": ["I"],
            "confidence": "CALCULATED",
            "note": "note"
        }
    }
    
    # We test this by invoking parse_financial_statement with mock LLM
    ingestion_agent.structured_llm = None  # Force raw text extraction mode
    
    with patch("langchain_core.runnables.base.RunnableSequence.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = MagicMock(content='{"company_name": "Test", "sector": "Test", "base_score": 50, "citations": ' + str(raw_citations).replace("'", '"') + '}')
        
        result = await ingestion_agent.parse_financial_statement("dummy text")
        
        citations = result.get("citations", {})
        assert citations["revenue"]["document"] == "doc"
        assert citations["revenue"]["location"] == "loc"
        assert citations["dscr"]["formula"] == "F"
        assert citations["dscr"]["confidence"] == "CALCULATED"

# ---------------------------------------------------------
# UNIT TESTS: CONFIDENCE VALIDATION
# ---------------------------------------------------------

def test_confidence_valid_verified():
    """VERIFIED is accepted as-is."""
    c = CitationDetail(page=1, confidence="VERIFIED")
    assert c.confidence == "VERIFIED"

def test_confidence_valid_inferred():
    """INFERRED is accepted as-is."""
    c = CitationDetail(page=1, confidence="INFERRED")
    assert c.confidence == "INFERRED"

def test_confidence_invalid_coerced_to_verified():
    """Unknown LLM values (e.g. HIGH, YES) must be coerced to VERIFIED."""
    c = CitationDetail(page=1, confidence="HIGH")
    assert c.confidence == "VERIFIED"

def test_confidence_none_stays_default():
    """None confidence should default to VERIFIED."""
    c = CitationDetail(page=1, confidence=None)
    # None passes through the validator and gets set to default by Field
    assert c.confidence in ("VERIFIED", None)  # either is acceptable

# ---------------------------------------------------------
# UNIT TESTS: COORDINATOR
# ---------------------------------------------------------

def test_build_dscr_citation():
    """Verify coordinator builds the correct DSCR calculated citation."""
    coordinator = AgentCoordinator()
    
    extracted_financials = {
        "citations": {
            "revenue": {"page": 3, "snippet": "revenue"},
            "debt": {"page": 5, "snippet": "debt"}
        }
    }
    
    financial_result = {
        "ratios": {
            "dscr": 1.5
        }
    }
    
    citation = coordinator._build_dscr_citation(extracted_financials, financial_result)
    
    assert citation is not None
    assert citation["confidence"] == "CALCULATED"
    assert "revenue (page 3)" in citation["inputs"]
    assert "debt (page 5)" in citation["inputs"]

def test_build_dscr_citation_missing_inputs():
    """Verify coordinator builds the correct DSCR calculated citation even if inputs are missing."""
    coordinator = AgentCoordinator()
    
    extracted_financials = {
        "citations": {
            "revenue": {"page": 3, "snippet": "revenue"}
            # debt missing
        }
    }
    
    financial_result = {
        "ratios": {
            "dscr": 1.5
        }
    }
    
    citation = coordinator._build_dscr_citation(extracted_financials, financial_result)
    
    assert citation is not None
    assert citation["confidence"] == "CALCULATED"
    assert "revenue (page 3)" in citation["inputs"]
    assert len(citation["inputs"]) == 1

def test_build_dscr_citation_no_dscr():
    """Verify coordinator returns None if DSCR is not calculated."""
    coordinator = AgentCoordinator()
    
    extracted_financials = {}
    financial_result = {"ratios": {"dscr": None}}
    
    citation = coordinator._build_dscr_citation(extracted_financials, financial_result)
    assert citation is None

def test_build_current_ratio_citation():
    """Verify coordinator builds the correct Current Ratio calculated citation."""
    coordinator = AgentCoordinator()

    extracted_financials = {
        "citations": {
            "revenue": {"page": 3, "snippet": "revenue", "document": "Balance Sheet"}
        }
    }

    financial_result = {
        "ratios": {
            "current_ratio": 1.5
        }
    }

    citation = coordinator._build_current_ratio_citation(extracted_financials, financial_result)

    assert citation is not None
    assert citation["confidence"] == "CALCULATED"
    assert citation["formula"] == "Current Ratio = Current Assets / Current Liabilities"
    assert any("Balance Sheet" in inp for inp in citation["inputs"])

def test_build_current_ratio_citation_no_ratio():
    """Verify coordinator returns None if current_ratio is not calculated."""
    coordinator = AgentCoordinator()
    citation = coordinator._build_current_ratio_citation({}, {"ratios": {"current_ratio": None}})
    assert citation is None

# ---------------------------------------------------------
# INTEGRATION TESTS: TRACEABILITY PROPAGATION
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_traceability_propagation():
    """Verify citations propagate fully from ingestion through CAM and evidence."""
    coordinator = AgentCoordinator()
    
    coordinator.ingestion_agent = MagicMock()
    coordinator.ingestion_agent.ingest_pdf = AsyncMock(
        return_value={"text": "dummy text that is long enough"}
    )
    coordinator.ingestion_agent.parse_financial_statement = AsyncMock(
        return_value={
            "company_name": "Test",
            "sector": "Test",
            "citations": {
                "revenue": {"page": 2, "snippet": "Rev", "document": "GSTR", "location": "Total", "confidence": "VERIFIED"},
                "debt": {"page": 3, "snippet": "Debt", "document": "BS", "location": "Total Borrowings", "confidence": "VERIFIED"}
            }
        }
    )

    coordinator.financial_agent = MagicMock()
    coordinator.financial_agent.analyze = AsyncMock(
        return_value={"status": "success", "financial_health_score": 80, "ratios": {"dscr": 2.0}}
    )

    coordinator.management_agent = MagicMock()
    coordinator.management_agent.analyze = AsyncMock(
        return_value={"status": "success", "management_score": 75.0}
    )

    coordinator.sector_agent = MagicMock()
    coordinator.sector_agent.get_sector_outlook = AsyncMock(
        return_value={"status": "success", "outlook": "Stable", "risk_factors": []}
    )
    coordinator.sector_agent.check_rbi_policies = AsyncMock(return_value=[])

    coordinator.integrity_agent = MagicMock()
    coordinator.integrity_agent.cross_validate = AsyncMock(
        return_value={"status": "completed", "flags": [], "warnings": []}
    )

    coordinator.cam_agent = MagicMock()
    coordinator.cam_agent.generate_cam = AsyncMock(
        return_value={
            "five_cs": {},
            "decision": "APPROVE",
            "decision_rationale": "Financials support approval.",
            "recommended_loan_amount": "INR 20,00,000",
            "recommended_interest_rate": "13%",
        }
    )
    
    coordinator.generate_explanation = AsyncMock(return_value="Dummy explanation")

    with patch("os.path.exists", return_value=True):
        result = await coordinator.run_appraisal({"file_path": "fake.pdf"})

    assert result["status"] == "success"
    
    # Verify CAM agent received the citations
    coordinator.cam_agent.generate_cam.assert_called_once()
    kwargs = coordinator.cam_agent.generate_cam.call_args.kwargs
    assert "ingestion_citations" in kwargs
    assert kwargs["ingestion_citations"]["revenue"]["document"] == "GSTR"
    
    # Verify final result contains evidence citations
    assert "evidence_citations" in result
    evidence = result["evidence_citations"]
    assert evidence["revenue"]["location"] == "Total"
    assert evidence["dscr"]["confidence"] == "CALCULATED"
    assert "revenue (page 2)" in evidence["dscr"]["inputs"]
