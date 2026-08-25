"""The ingest route must link the case it created to the appraisal it produced.

This is the gap the Phase 2 tests missed. They seeded the link directly and
asserted that a linked case exposes its appraisal - which was true - while the
real pipeline never created the link at all, because:

  * ``run_appraisal_with_state()`` generates its own case_id when the caller
    supplies none (the single-file upload path never does), and
  * the route only ever read its own ``case_id`` form field, which was None.

The result on a real run was an appraisal row with ``case_id`` NULL and a case
row with NULL borrower_name / analysis_status / decision_allowed, so the case
workspace showed "Not recorded" everywhere for a completed analysis.

These tests exercise the wiring rather than the mechanism.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.database.database import get_sqlite_connection, init_db
from app.main import app
from app.security.auth_service import hash_password

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()


@pytest.fixture(autouse=True)
def set_audit_hmac_secret(monkeypatch):
    monkeypatch.setenv("AUDIT_HMAC_SECRET", "dummy_test_secret_for_audit_logs")


def _truncate():
    conn = get_sqlite_connection()
    c = conn.cursor()
    for table in ("sessions", "tenant_memberships", "users", "loan_cases", "appraisal_records"):
        c.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_db():
    _truncate()
    yield
    _truncate()


def make_analyst(email="analyst@wiring.com", password="password123"):
    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id, tenant_id = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute(
        "INSERT INTO users (id, email, password_hash, is_active, is_locked, failed_login_count, "
        "mfa_enabled) VALUES (?, ?, ?, 1, 0, 0, 0)",
        (user_id, email, hash_password(password)),
    )
    c.execute(
        "INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) "
        "VALUES (?, ?, 'CREDIT_ANALYST', 1)",
        (user_id, tenant_id),
    )
    conn.commit()
    conn.close()
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"], tenant_id


COORDINATOR_CASE_ID = "CASE_ABCDEF123456"


def coordinator_result(decision="APPROVE", company="Titan Alloy Castings Private Limited"):
    """A coordinator return value shaped like the real one, including case_id."""
    return {
        "status": "success",
        # The coordinator reports the case it created. The route must use this.
        "case_id": COORDINATOR_CASE_ID,
        "appraisal_id": "APPRAISAL_1787600000",
        "individual_agent_outputs": {
            "ingestion": {
                "company_name": company,
                "sector": "Manufacturing",
                "base_score": 60,
                "total_revenue": 380000000,
            },
            "financial_health": {
                "financial_health_score": 60,
                "ratios": {"dscr": 1.4, "current_ratio": 1.3},
                "metrics": {"revenue": 380000000, "total_debt": 120000000},
            },
            "sector_context": {"sector": "Manufacturing"},
            "management_quality": {"management_score": 70},
            "integrity_check": {"flags_detected": 0, "flags": []},
        },
        "combined_decision": {
            "decision": decision,
            "recommended_loan_amount": "38 Cr",
            "recommended_interest_rate": "11.5%",
            "decision_rationale": "Adequate coverage.",
            "five_cs": {},
        },
        "evidence_trail": [],
        "explanation": "",
    }


def upload(token, result):
    """Drive POST /documents/ingest/pdf with the coordinator stubbed out."""
    with patch(
        "app.agents.orchestration.coordinator.AgentCoordinator.run_appraisal_with_state",
        new_callable=AsyncMock,
    ) as run, patch("app.routes.documents.run_pdf_forensics", return_value={"is_suspicious": False}), patch(
        "app.agents.security.document_security.DocumentSecurityAgent.scan_file"
    ) as scan:
        run.return_value = result
        scan.return_value = type("Scan", (), {"is_safe": True, "warnings": {}, "flags": []})()
        return client.post(
            "/api/v1/documents/ingest/pdf",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("statement.pdf", b"%PDF-1.4 minimal", "application/pdf")},
        )


def read_case(case_id):
    conn = get_sqlite_connection()
    try:
        row = conn.execute(
            "SELECT borrower_name, analysis_status, decision_allowed, appraisal_id, result_data "
            "FROM loan_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
    finally:
        conn.close()
    return row


class TestIngestLinksCaseToAppraisal:
    def test_appraisal_records_the_coordinator_case_id(self):
        """The regression: case_id was NULL on every real single-file run."""
        token, tenant = make_analyst()
        # The coordinator normally creates this row itself.
        conn = get_sqlite_connection()
        conn.execute(
            "INSERT INTO loan_cases (case_id, status, institution_id, input_data, result_data) "
            "VALUES (?, 'COMPLETED', ?, '{}', '{}')",
            (COORDINATOR_CASE_ID, tenant),
        )
        conn.commit()
        conn.close()

        res = upload(token, coordinator_result())
        assert res.status_code == 200, res.text

        conn = get_sqlite_connection()
        try:
            rows = conn.execute(
                "SELECT id, case_id FROM appraisal_records WHERE institution_id = ?", (tenant,)
            ).fetchall()
        finally:
            conn.close()

        assert rows, "an appraisal record should have been written"
        assert all(r[1] == COORDINATOR_CASE_ID for r in rows), (
            "appraisal_records.case_id must carry the coordinator's case id, not NULL"
        )

    def test_case_row_is_populated_after_a_run(self):
        token, tenant = make_analyst()
        conn = get_sqlite_connection()
        conn.execute(
            "INSERT INTO loan_cases (case_id, status, institution_id, input_data, result_data) "
            "VALUES (?, 'COMPLETED', ?, '{}', '{}')",
            (COORDINATOR_CASE_ID, tenant),
        )
        conn.commit()
        conn.close()

        assert upload(token, coordinator_result()).status_code == 200

        borrower, analysis_status, decision_allowed, appraisal_id, _ = read_case(COORDINATOR_CASE_ID)
        assert borrower == "Titan Alloy Castings Private Limited"
        assert analysis_status is not None, "analysis_status must be recorded, not left NULL"
        assert decision_allowed is not None, "decision_allowed must be recorded, not left NULL"
        assert appraisal_id is not None, "the case must know which appraisal it produced"

    def test_result_data_carries_the_gate_outcome(self):
        """The coordinator snapshots result_data before the gate is applied.

        The route must re-persist the gated result, otherwise a completed case
        reports "Not recorded" for its analysis status.
        """
        token, tenant = make_analyst()
        conn = get_sqlite_connection()
        conn.execute(
            "INSERT INTO loan_cases (case_id, status, institution_id, input_data, result_data) "
            "VALUES (?, 'COMPLETED', ?, '{}', '{}')",
            (COORDINATOR_CASE_ID, tenant),
        )
        conn.commit()
        conn.close()

        assert upload(token, coordinator_result()).status_code == 200

        _, _, _, _, result_data = read_case(COORDINATOR_CASE_ID)
        payload = json.loads(result_data)
        assert "analysis_status" in payload
        assert "decision_allowed" in payload

    def test_case_workspace_reaches_the_appraisal_after_a_real_run(self):
        """End to end: upload, then read the case back through the API."""
        token, tenant = make_analyst()
        conn = get_sqlite_connection()
        conn.execute(
            "INSERT INTO loan_cases (case_id, status, institution_id, input_data, result_data) "
            "VALUES (?, 'COMPLETED', ?, '{}', '{}')",
            (COORDINATOR_CASE_ID, tenant),
        )
        conn.commit()
        conn.close()

        assert upload(token, coordinator_result()).status_code == 200

        res = client.get(
            f"/api/v1/cases/{COORDINATOR_CASE_ID}", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["appraisal"] is not None, (
            "the case workspace must find the appraisal produced by this run"
        )
        assert body["case"]["borrower_name"] == "Titan Alloy Castings Private Limited"

    def test_placeholder_borrower_name_is_not_written(self):
        """A failed extraction must not stamp "Unknown Entity" onto the case."""
        token, tenant = make_analyst()
        conn = get_sqlite_connection()
        conn.execute(
            "INSERT INTO loan_cases (case_id, status, institution_id, input_data, result_data) "
            "VALUES (?, 'COMPLETED', ?, '{}', '{}')",
            (COORDINATOR_CASE_ID, tenant),
        )
        conn.commit()
        conn.close()

        assert upload(token, coordinator_result(company="Unknown Entity")).status_code == 200

        borrower, _, _, _, _ = read_case(COORDINATOR_CASE_ID)
        assert borrower is None, "an unextracted borrower stays NULL rather than a placeholder"
