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

def test_financial_health_endpoint(client):
    """
    Test the /api/v1/analysis/financial-health endpoint.
    """
    response = client.get("/api/v1/analysis/financial-health?company_name=TestCompany")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["company_name"] == "TestCompany"
    assert "financial_health_score" in data
    assert "ratios" in data
    assert "cash_flow_assessment" in data
    assert data["ratios"]["current_ratio"] == 1.85

def test_management_quality_endpoint(client):
    """
    Test the /api/v1/analysis/management-quality endpoint.
    """
    response = client.get("/api/v1/analysis/management-quality?company_name=TestCompany")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["company_name"] == "TestCompany"
    assert "management_score" in data
    assert "promoter_analysis" in data
    assert "governance_assessment" in data
    assert len(data["promoter_analysis"]) > 0

def test_sector_context_endpoint(client):
    """
    Test the /api/v1/analysis/sector-context endpoint.
    """
    response = client.get("/api/v1/analysis/sector-context?sector=Technology")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["sector"] == "Technology"
    assert "outlook" in data
    assert "growth_rate_projected" in data
    assert "rbi_policy_impact" in data

