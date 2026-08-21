import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_fetch_gst_success(client, admin_headers):
    payload = {"gstin": "22AAAAA0000A1Z5"}
    response = client.post("/api/v1/data/gst", json=payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "gstin" in data["data"]
    assert data["data"]["gstin"] == payload["gstin"]

def test_fetch_gst_invalid_length(client, admin_headers):
    payload = {"gstin": "123"}
    response = client.post("/api/v1/data/gst", json=payload, headers=admin_headers)
    assert response.status_code == 422

def test_fetch_itr_success(client, admin_headers):
    payload = {"pan": "ABCDE1234F"}
    response = client.post("/api/v1/data/itr", json=payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "pan" in data["data"]

def test_fetch_itr_invalid_length(client, admin_headers):
    payload = {"pan": "ABC"}
    response = client.post("/api/v1/data/itr", json=payload, headers=admin_headers)
    assert response.status_code == 422

def test_fetch_bank_statement_success(client, admin_headers):
    payload = {"account_id": "ACC123456"}
    response = client.post("/api/v1/data/bank-statement", json=payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "account_id" in data["data"]

def test_fetch_bank_statement_missing_field(client, admin_headers):
    payload = {}
    response = client.post("/api/v1/data/bank-statement", json=payload, headers=admin_headers)
    assert response.status_code == 422
