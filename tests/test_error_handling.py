from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_integrity_check_error_handling(client, admin_headers):
    # Sending invalid/empty request body to trigger parse error
    response = client.post("/api/v1/analysis/integrity-check", content="", headers=admin_headers)
    assert response.status_code == 400
    data = response.json()
    assert data["status"] == "error"
    assert "message" in data

def test_web_research_error_handling(client, admin_headers):
    # Sending invalid/empty request body to trigger parse error
    response = client.post("/api/v1/research/web-research", content="", headers=admin_headers)
    assert response.status_code == 400
    data = response.json()
    assert data["status"] == "error"
    assert "message" in data

def test_adjust_score_error_handling(client, admin_headers):
    # Sending invalid/empty request body to trigger parse error
    response = client.post("/api/v1/research/adjust-score", content="", headers=admin_headers)
    assert response.status_code == 400
    data = response.json()
    assert data["status"] == "error"
    assert "message" in data
