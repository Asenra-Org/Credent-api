from unittest.mock import patch

import pytest
import uuid
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_celery_dispatch():
    with patch('app.queue.celery_app.celery_app.send_task') as mock_send:
        mock_send.return_value = type('obj', (object,), {'id': 'dummy'})
        yield mock_send


def test_upload_missing_all_fields():
    response = client.post("/api/v1/documents/ingest/pdf", headers={"X-Tenant-ID": "test_tenant"})
    assert response.status_code == 400
    assert "at least one document" in response.json()["detail"].lower()

def test_upload_financials_only():
    files = {"financials": ("fin.pdf", b"%PDF-1.4...", "application/pdf")}
    headers = {"X-Tenant-ID": "test_tenant", "Idempotency-Key": f"test_idempotency_123_{uuid.uuid4().hex}"}
    response = client.post("/api/v1/documents/ingest/pdf", files=files, headers=headers)
    if response.status_code != 202:
        print(response.json())
    assert response.status_code == 202

def test_upload_mixed_roles():
    files = [
        ("financials", ("fin.pdf", b"%PDF-1.4...", "application/pdf")),
        ("gst_returns", ("gst.pdf", b"%PDF-1.4...", "application/pdf"))
    ]
    headers = {"X-Tenant-ID": "test_tenant", "Idempotency-Key": f"test_idempotency_456_{uuid.uuid4().hex}"}
    response = client.post("/api/v1/documents/ingest/pdf", files=files, headers=headers)
    if response.status_code != 202:
        print(response.json())
    assert response.status_code == 202

def test_upload_aggregate_size_limit():
    large_buffer = b"0" * (11 * 1024 * 1024) # 11 MB
    files = [
        ("financials", ("fin1.pdf", large_buffer, "application/pdf")),
        ("bank_statements", ("bank1.pdf", large_buffer, "application/pdf"))
    ]
    # Total size 22MB, exceeds 20MB aggregate
    headers = {"X-Tenant-ID": "test_tenant", "Idempotency-Key": f"test_idempotency_789_{uuid.uuid4().hex}"}
    response = client.post("/api/v1/documents/ingest/pdf", files=files, headers=headers)
    assert response.status_code == 413
    assert "aggregate payload too large" in response.json()["detail"].lower()

def test_idempotency_different_files():
    files1 = {"financials": ("fin1.pdf", b"%PDF-1.4-V1", "application/pdf")}
    files2 = {"financials": ("fin1.pdf", b"%PDF-1.4-V2", "application/pdf")}
    headers = {"X-Tenant-ID": "test_tenant", "Idempotency-Key": f"same_key_test_{uuid.uuid4().hex}"}
    
    res1 = client.post("/api/v1/documents/ingest/pdf", files=files1, headers=headers)
    res2 = client.post("/api/v1/documents/ingest/pdf", files=files2, headers=headers)
    
    if res1.status_code != 202:
        print(res1.json())
    assert res1.status_code == 202
    assert res2.status_code == 409 # Conflict because idempotency key is same but file content changed (fingerprint mismatch)
