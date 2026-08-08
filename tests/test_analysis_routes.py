from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_financial_health_route():
    response = client.get("/api/v1/analysis/financial-health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "financial_health_score" in data

def test_management_quality_route():
    response = client.get("/api/v1/analysis/management-quality")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "promoter_analysis" in data

def test_sector_context_route():
    response = client.get("/api/v1/analysis/sector-context")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "outlook" in data
