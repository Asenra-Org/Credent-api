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
    headers = {"X-Tenant-ID": "test_tenant", "Idempotency-Key": "test_idem_" + str(__import__("uuid").uuid4())}
    response = client.post("/api/v1/documents/ingest/pdf", files=files, headers=headers)
    
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
    assert "current_ratio" in data["ratios"]

def test_management_quality_endpoint(client):
    """
    Test the /api/v1/analysis/management-quality endpoint.
    """
    response = client.get("/api/v1/analysis/management-quality?company_name=TestCompany")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["company_name"] == "TestCompany"
    assert data["management_score"] == 0.0
    assert data["requires_manual_review"] is True
    assert "promoter_analysis" in data
    assert "governance_assessment" in data
    assert len(data["promoter_analysis"]) == 0

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


def test_save_and_fetch_promoter_appraisal():
    """
    Test that save_appraisal correctly persists promoter results and governance scores,
    and get_recent_appraisals correctly retrieves them.
    """
    from app.database.database import save_appraisal, get_recent_appraisals
    
    test_data = {
        "company_id": "CMP_TEST_DB",
        "company_name": "Database Test Corp",
        "sector": "Technology",
        "revenue": "10.0 Cr",
        "debt": "2.0 Cr",
        "base_score": 700,
        "adjusted_score": 750,
        "decision": "APPROVE",
        "recommended_loan_amount": "3.0 Cr",
        "recommended_interest_rate": "10.0%",
        "decision_rationale": "Solid metrics.",
        "raw_document_data": {"company_name": "Database Test Corp"},
        "integrity_flags": {"fraud_detected": False},
        "web_research": {"sentiment": "Neutral"},
        "cam_report": {"summary": "Strong growth profile"},
        
        # New database columns
        "management_score": 88.5,
        "promoter_analysis": [
            {
                "name": "Jane Smith",
                "experience_years": 14,
                "risk_flags": [],
                "verdict": "Clear record"
            }
        ],
        "governance_assessment": {
            "board_independence": "Good",
            "regulatory_compliance": "Fully Compliant",
            "risk_level": "Low"
        }
    }
    
    # Save the appraisal
    record_id = save_appraisal(test_data)
    assert record_id is not None
    
    # Fetch recent appraisals
    recent = get_recent_appraisals(limit=5)
    
    # Find our saved record
    target_record = None
    for r in recent:
        if r.get("company_name") == "Database Test Corp":
            target_record = r
            break
            
    assert target_record is not None
    assert target_record["management_score"] == 88.5
    assert len(target_record["promoter_analysis"]) == 1
    assert target_record["promoter_analysis"][0]["name"] == "Jane Smith"
    assert target_record["governance_assessment"]["board_independence"] == "Good"


