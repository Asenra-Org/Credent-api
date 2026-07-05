def test_health_check(client):
    """
    Test the /health endpoint returns a 200 status and correct format.
    """
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["healthy", "degraded"]

def test_empty_file_upload(client):
    """
    Test boundary upload with an empty file to the ingestion endpoint.
    """
    # Endpoint expects multipart/form-data with a 'file'
    files = {"file": ("empty.pdf", b"", "application/pdf")}
    response = client.post("/api/v1/documents/ingest/pdf", files=files)
    
    # Based on our routes/documents.py logic, it should return 400 with a specific message
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Uploaded file is empty."
