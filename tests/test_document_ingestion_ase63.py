import pytest
from app.agents.input.document_ingestion import RiskExtraction

def test_valid_numeric():
    data = {"company_name": "Co", "sector": "Sec", "base_score": 50, "total_debt": 500.50}
    model = RiskExtraction(**data)
    assert model.total_debt == 500.50

def test_missing_field():
    data = {"company_name": "Co", "sector": "Sec", "base_score": 50}
    model = RiskExtraction(**data)
    assert model.total_debt is None

def test_na_string():
    data = {"company_name": "Co", "sector": "Sec", "base_score": 50, "total_debt": "N/A"}
    model = RiskExtraction(**data)
    assert model.total_debt is None

def test_unknown_string():
    data = {"company_name": "Co", "sector": "Sec", "base_score": 50, "total_debt": "Unknown value"}
    model = RiskExtraction(**data)
    assert model.total_debt is None

def test_malformed_numeric():
    # Commas should be stripped
    data = {"company_name": "Co", "sector": "Sec", "base_score": 50, "total_debt": "1,00,000.50"}
    model = RiskExtraction(**data)
    assert model.total_debt == 100000.50

def test_invalid_enum():
    # pydantic confidence enum fallback
    data = {
        "company_name": "Co", "sector": "Sec", "base_score": 50,
        "citations": {
            "revenue": {"confidence": "UNKNOWN"}
        }
    }
    model = RiskExtraction(**data)
    assert model.citations.revenue.confidence == "VERIFIED"
