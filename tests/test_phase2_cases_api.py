"""Phase 2 - /cases and /audit endpoints against real database rows.

Nothing here is mocked below the HTTP layer: each test seeds real rows into the
same SQLite database the application uses, logs in through the real auth flow,
and asserts on what the endpoints return. That is the only way to verify tenant
isolation, since isolation is enforced in SQL rather than in a response builder.

Coverage:
  * tenant isolation on /cases, /cases/{id}, /cases/{id}/audit, /audit/events
  * role authorization for every endpoint added in Phase 2
  * case -> appraisal -> CAM traceability
  * the P0-4 gate surviving the round trip through the API
  * append-only audit logs staying append-only
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database.database import get_sqlite_connection, init_db, link_case_appraisal
from app.main import app
from app.security.auth_service import hash_password

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()


@pytest.fixture(autouse=True)
def set_audit_hmac_secret(monkeypatch):
    monkeypatch.setenv("AUDIT_HMAC_SECRET", "dummy_test_secret_for_audit_logs")


def _truncate_test_tables():
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute("DELETE FROM sessions")
    c.execute("DELETE FROM tenant_memberships")
    c.execute("DELETE FROM users")
    c.execute("DELETE FROM loan_cases")
    c.execute("DELETE FROM case_documents")
    c.execute("DELETE FROM appraisal_records")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_db():
    """Reset identity and the Phase 2 tables around every test.

    Cleaning on both sides matters: cleaning only on entry leaves the last
    test's rows sitting in the developer database afterwards.

    audit_logs is append-only by trigger and is never deleted from here. Tests
    assert on events they created, identified by their own tenant id.
    """
    _truncate_test_tables()
    yield
    _truncate_test_tables()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_user(email, password="password123", role="CREDIT_ANALYST", tenant_id=None):
    """Create an active user with a membership, and return (user_id, tenant_id)."""
    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    tenant_id = tenant_id or str(uuid.uuid4())
    c.execute(
        "INSERT INTO users (id, email, password_hash, mfa_enabled, is_active, is_locked, "
        "failed_login_count) VALUES (?, ?, ?, 0, 1, 0, 0)",
        (user_id, email, hash_password(password)),
    )
    c.execute(
        "INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)",
        (user_id, tenant_id, role),
    )
    conn.commit()
    conn.close()
    return user_id, tenant_id


def login(email, password="password123"):
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def seed_case(tenant_id, case_id=None, status="COMPLETED", borrower=None, result=None, **columns):
    """Insert one loan_cases row and return its case_id."""
    case_id = case_id or uuid.uuid4().hex
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO loan_cases (case_id, status, current_step, institution_id, input_data, "
        "result_data, borrower_name) VALUES (?, ?, 'done', ?, '{}', ?, ?)",
        (case_id, status, tenant_id, json.dumps(result or {}), borrower),
    )
    for column, value in columns.items():
        c.execute(f"UPDATE loan_cases SET {column} = ? WHERE case_id = ?", (value, case_id))
    conn.commit()
    conn.close()
    return case_id


def seed_appraisal(tenant_id, case_id, decision="APPROVE", cam=None, decision_allowed=1):
    """Insert one appraisal_records row linked to a case."""
    appraisal_id = "REC_" + uuid.uuid4().hex[:12]
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO appraisal_records (id, company_id, decision, decision_rationale, "
        "adjusted_score, base_score, cam_report, institution_id, case_id, analysis_status, "
        "decision_allowed, model_provider, model_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            appraisal_id, "comp_1", decision, "rationale", 72, 70,
            json.dumps(cam or {"executive_summary": {"revenue": "500 Cr"}}),
            tenant_id, case_id, "COMPLETED", decision_allowed, "groq", "openai/gpt-oss-20b",
        ),
    )
    conn.commit()
    conn.close()
    return appraisal_id


def seed_audit_event(tenant_id, user_id, action="CASE_CREATED", case_id=None):
    """Write one audit event through the real hash-chain service."""
    from app.security.audit_service import create_audit_event

    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event_id = create_audit_event(
            conn=conn, tenant_id=tenant_id, user_id=user_id, action=action,
            case_id=case_id, resource_type="loan_cases", resource_id=case_id,
        )
        conn.commit()
    finally:
        conn.close()
    return event_id


# ---------------------------------------------------------------------------
# 1. real data round trip
# ---------------------------------------------------------------------------

class TestCasesReturnRealData:
    def test_list_returns_the_seeded_case(self):
        _, tenant = make_user("analyst@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant, borrower="Acme Steel Ltd")
        token = login("analyst@a.com")

        res = client.get("/api/v1/cases", headers=auth(token))
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert body["items"][0]["case_id"] == case_id
        assert body["items"][0]["borrower_name"] == "Acme Steel Ltd"

    def test_unrecorded_fields_are_null_not_invented(self):
        """A case with no borrower name reads as null, never as a placeholder."""
        _, tenant = make_user("analyst@a.com", role="CREDIT_ANALYST")
        seed_case(tenant, borrower=None)
        token = login("analyst@a.com")

        item = client.get("/api/v1/cases", headers=auth(token)).json()["items"][0]
        assert item["borrower_name"] is None
        assert item["requested_amount"] is None
        assert item["assigned_to"] is None
        for value in item.values():
            assert value != "Unknown"
            assert value != "N/A"

    def test_empty_tenant_returns_an_empty_page_not_placeholder_rows(self):
        make_user("analyst@a.com", role="CREDIT_ANALYST")
        token = login("analyst@a.com")

        body = client.get("/api/v1/cases", headers=auth(token)).json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_pagination_reports_real_totals(self):
        _, tenant = make_user("analyst@a.com", role="CREDIT_ANALYST")
        for _ in range(5):
            seed_case(tenant)
        token = login("analyst@a.com")

        body = client.get("/api/v1/cases?limit=2&offset=0", headers=auth(token)).json()
        assert body["total"] == 5
        assert body["returned"] == 2
        assert len(body["items"]) == 2

    def test_search_filters_on_borrower_name(self):
        _, tenant = make_user("analyst@a.com", role="CREDIT_ANALYST")
        seed_case(tenant, borrower="Acme Steel Ltd")
        seed_case(tenant, borrower="Zenith Textiles")
        token = login("analyst@a.com")

        body = client.get("/api/v1/cases?search=acme", headers=auth(token)).json()
        assert body["total"] == 1
        assert body["items"][0]["borrower_name"] == "Acme Steel Ltd"

    def test_status_filter_uses_the_canonical_vocabulary(self):
        _, tenant = make_user("analyst@a.com", role="CREDIT_ANALYST")
        seed_case(tenant, status="COMPLETED")
        seed_case(tenant, status="RUNNING")
        token = login("analyst@a.com")

        body = client.get("/api/v1/cases?status=READY_FOR_REVIEW", headers=auth(token)).json()
        assert body["total"] == 1
        assert body["items"][0]["lifecycle_status"] == "READY_FOR_REVIEW"

    def test_unknown_status_filter_is_rejected(self):
        make_user("analyst@a.com", role="CREDIT_ANALYST")
        token = login("analyst@a.com")

        res = client.get("/api/v1/cases?status=UNKNOWN", headers=auth(token))
        assert res.status_code == 400
        assert "UNKNOWN" in res.json()["detail"]["invalid"]

    def test_statuses_endpoint_publishes_the_closed_set(self):
        make_user("analyst@a.com", role="CREDIT_ANALYST")
        token = login("analyst@a.com")

        body = client.get("/api/v1/cases/statuses", headers=auth(token)).json()
        assert len(body["statuses"]) == 12
        assert "ANALYSIS_INCOMPLETE" in body["statuses"]
        assert "UNKNOWN" not in body["statuses"]


# ---------------------------------------------------------------------------
# 2. tenant isolation
# ---------------------------------------------------------------------------

class TestTenantIsolation:
    def test_list_never_shows_another_tenants_cases(self):
        _, tenant_a = make_user("a@a.com", role="CREDIT_ANALYST")
        _, tenant_b = make_user("b@b.com", role="CREDIT_ANALYST")
        seed_case(tenant_a, borrower="Tenant A Borrower")
        seed_case(tenant_b, borrower="Tenant B Borrower")

        body = client.get("/api/v1/cases", headers=auth(login("a@a.com"))).json()
        assert body["total"] == 1
        assert body["items"][0]["borrower_name"] == "Tenant A Borrower"

        body_b = client.get("/api/v1/cases", headers=auth(login("b@b.com"))).json()
        assert body_b["total"] == 1
        assert body_b["items"][0]["borrower_name"] == "Tenant B Borrower"

    def test_detail_of_another_tenants_case_is_404(self):
        _, tenant_a = make_user("a@a.com", role="CREDIT_ANALYST")
        _, tenant_b = make_user("b@b.com", role="CREDIT_ANALYST")
        case_b = seed_case(tenant_b)

        res = client.get(f"/api/v1/cases/{case_b}", headers=auth(login("a@a.com")))
        assert res.status_code == 404

    def test_documents_of_another_tenants_case_are_404(self):
        make_user("a@a.com", role="CREDIT_ANALYST")
        _, tenant_b = make_user("b@b.com", role="CREDIT_ANALYST")
        case_b = seed_case(tenant_b)

        res = client.get(f"/api/v1/cases/{case_b}/documents", headers=auth(login("a@a.com")))
        assert res.status_code == 404

    def test_case_audit_of_another_tenants_case_is_404(self):
        make_user("a@a.com", role="CREDIT_ANALYST")
        user_b, tenant_b = make_user("b@b.com", role="CREDIT_ANALYST")
        case_b = seed_case(tenant_b)
        seed_audit_event(tenant_b, user_b, case_id=case_b)

        res = client.get(f"/api/v1/cases/{case_b}/audit", headers=auth(login("a@a.com")))
        assert res.status_code == 404

    def test_audit_events_are_scoped_to_the_callers_organization(self):
        user_a, tenant_a = make_user("a@a.com", role="ORG_ADMIN")
        user_b, tenant_b = make_user("b@b.com", role="ORG_ADMIN")
        seed_audit_event(tenant_a, user_a, action="TENANT_A_EVENT")
        seed_audit_event(tenant_b, user_b, action="TENANT_B_EVENT")

        body = client.get("/api/v1/audit/events", headers=auth(login("a@a.com"))).json()
        assert body["organization_id"] == tenant_a
        actions = {e["action"] for e in body["items"]}
        assert "TENANT_B_EVENT" not in actions
        assert all(e["tenant_id"] == tenant_a for e in body["items"])

    def test_org_admin_cannot_read_another_organizations_audit_log(self):
        make_user("a@a.com", role="ORG_ADMIN")
        _, tenant_b = make_user("b@b.com", role="ORG_ADMIN")

        res = client.get(
            f"/api/v1/audit/events?organization_id={tenant_b}", headers=auth(login("a@a.com"))
        )
        assert res.status_code == 403

    def test_super_admin_may_name_another_organization(self):
        make_user("root@platform.com", role="SUPER_ADMIN")
        user_b, tenant_b = make_user("b@b.com", role="ORG_ADMIN")
        seed_audit_event(tenant_b, user_b, action="TENANT_B_EVENT")

        res = client.get(
            f"/api/v1/audit/events?organization_id={tenant_b}",
            headers=auth(login("root@platform.com")),
        )
        assert res.status_code == 200
        assert res.json()["organization_id"] == tenant_b

    def test_appraisal_link_does_not_cross_tenants(self):
        """A case id colliding across tenants must not leak the other's CAM."""
        _, tenant_a = make_user("a@a.com", role="CREDIT_ANALYST")
        _, tenant_b = make_user("b@b.com", role="CREDIT_ANALYST")
        shared_case_id = uuid.uuid4().hex
        seed_case(tenant_a, case_id=shared_case_id)
        seed_appraisal(tenant_b, shared_case_id, cam={"secret": "tenant B CAM"})

        body = client.get(f"/api/v1/cases/{shared_case_id}", headers=auth(login("a@a.com"))).json()
        assert body["appraisal"] is None


# ---------------------------------------------------------------------------
# 3. role authorization
# ---------------------------------------------------------------------------

class TestRoleAuthorization:
    @pytest.mark.parametrize("role", ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN", "VIEWER"])
    def test_case_readers_can_list_cases(self, role):
        make_user("u@u.com", role=role)
        res = client.get("/api/v1/cases", headers=auth(login("u@u.com")))
        assert res.status_code == 200

    def test_super_admin_is_not_a_case_reader(self):
        """A platform operator has no business reading a tenant's credit cases."""
        make_user("root@platform.com", role="SUPER_ADMIN")
        res = client.get("/api/v1/cases", headers=auth(login("root@platform.com")))
        assert res.status_code == 403

    def test_viewer_cannot_read_the_case_audit_trail(self):
        _, tenant = make_user("v@v.com", role="VIEWER")
        case_id = seed_case(tenant)
        res = client.get(f"/api/v1/cases/{case_id}/audit", headers=auth(login("v@v.com")))
        assert res.status_code == 403

    @pytest.mark.parametrize("role", ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN"])
    def test_case_audit_readers_can_read_a_case_trail(self, role):
        _, tenant = make_user("u@u.com", role=role)
        case_id = seed_case(tenant)
        res = client.get(f"/api/v1/cases/{case_id}/audit", headers=auth(login("u@u.com")))
        assert res.status_code == 200

    @pytest.mark.parametrize("role", ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "VIEWER"])
    def test_audit_explorer_is_refused_below_org_admin(self, role):
        make_user("u@u.com", role=role)
        res = client.get("/api/v1/audit/events", headers=auth(login("u@u.com")))
        assert res.status_code == 403

    @pytest.mark.parametrize("role", ["ORG_ADMIN", "SUPER_ADMIN"])
    def test_audit_explorer_allows_admins(self, role):
        make_user("u@u.com", role=role)
        res = client.get("/api/v1/audit/events", headers=auth(login("u@u.com")))
        assert res.status_code == 200

    @pytest.mark.parametrize("path", [
        "/api/v1/cases",
        "/api/v1/cases/statuses",
        "/api/v1/cases/some-id",
        "/api/v1/cases/some-id/documents",
        "/api/v1/cases/some-id/audit",
        "/api/v1/audit/events",
        "/api/v1/audit/verify",
    ])
    def test_every_new_endpoint_refuses_an_anonymous_caller(self, path):
        res = client.get(path)
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path", [
        "/api/v1/cases",
        "/api/v1/audit/events",
    ])
    def test_every_new_endpoint_refuses_a_forged_token(self, path):
        res = client.get(path, headers={"Authorization": "Bearer not-a-real-token"})
        assert res.status_code == 401

    def test_deactivated_user_is_refused(self):
        make_user("gone@a.com", role="CREDIT_ANALYST")
        token = login("gone@a.com")
        conn = get_sqlite_connection()
        conn.execute("UPDATE users SET is_active = 0 WHERE email = ?", ("gone@a.com",))
        conn.commit()
        conn.close()

        res = client.get("/api/v1/cases", headers=auth(token))
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# 4. case -> appraisal -> CAM traceability
# ---------------------------------------------------------------------------

class TestTraceability:
    def test_case_workspace_reaches_its_own_cam(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant, borrower="Acme Steel Ltd")
        appraisal_id = seed_appraisal(
            tenant, case_id, cam={"executive_summary": {"revenue": "500 Cr"}}
        )

        body = client.get(f"/api/v1/cases/{case_id}", headers=auth(login("a@a.com"))).json()
        assert body["appraisal"] is not None
        assert body["appraisal"]["id"] == appraisal_id
        assert body["appraisal"]["cam_report"]["executive_summary"]["revenue"] == "500 Cr"

    def test_appraisal_is_null_when_none_is_linked(self):
        """An unfinished case reports no appraisal rather than an empty one."""
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant, status="RUNNING")

        body = client.get(f"/api/v1/cases/{case_id}", headers=auth(login("a@a.com"))).json()
        assert body["appraisal"] is None

    def test_provenance_travels_with_the_appraisal(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant)
        seed_appraisal(tenant, case_id)

        appraisal = client.get(
            f"/api/v1/cases/{case_id}", headers=auth(login("a@a.com"))
        ).json()["appraisal"]
        assert appraisal["model_provider"] == "groq"
        assert appraisal["model_name"] == "openai/gpt-oss-20b"

    def test_link_case_appraisal_records_the_gate_outcome(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant, status="COMPLETED")
        link_case_appraisal(
            case_id=case_id, appraisal_id="REC_abc", analysis_status="FAILED",
            decision_allowed=False, decision="ANALYSIS_INCOMPLETE",
        )

        item = client.get("/api/v1/cases", headers=auth(login("a@a.com"))).json()["items"][0]
        assert item["appraisal_id"] == "REC_abc"
        assert item["decision_allowed"] is False
        assert item["lifecycle_status"] == "ANALYSIS_INCOMPLETE"

    def test_link_does_not_blank_columns_it_was_not_given(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant, borrower="Acme Steel Ltd")
        link_case_appraisal(case_id=case_id, appraisal_id="REC_abc")

        item = client.get("/api/v1/cases", headers=auth(login("a@a.com"))).json()["items"][0]
        assert item["borrower_name"] == "Acme Steel Ltd"


# ---------------------------------------------------------------------------
# 5. the P0-4 gate survives the API round trip
# ---------------------------------------------------------------------------

class TestFailClosedThroughTheApi:
    def test_incomplete_analysis_is_reported_as_incomplete_not_reviewable(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        seed_case(
            tenant, status="COMPLETED",
            result={
                "analysis_status": "FAILED",
                "decision_allowed": False,
                "missing_required": ["financial_health"],
                "combined_decision": {"decision": "ANALYSIS_INCOMPLETE"},
            },
        )

        item = client.get("/api/v1/cases", headers=auth(login("a@a.com"))).json()["items"][0]
        assert item["lifecycle_status"] == "ANALYSIS_INCOMPLETE"
        assert item["decision_allowed"] is False
        assert item["missing_required"] == ["financial_health"]

    def test_incomplete_analysis_never_surfaces_as_manual_review(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        seed_case(
            tenant, status="COMPLETED",
            result={"analysis_status": "FAILED", "decision_allowed": False,
                    "combined_decision": {"decision": "ANALYSIS_INCOMPLETE"}},
        )

        item = client.get("/api/v1/cases", headers=auth(login("a@a.com"))).json()["items"][0]
        assert item["lifecycle_status"] != "MANUAL_REVIEW"

    def test_degraded_components_are_surfaced_verbatim(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        seed_case(
            tenant, status="COMPLETED",
            result={"analysis_status": "DEGRADED", "decision_allowed": True,
                    "degraded_components": ["sector_context"],
                    "combined_decision": {"decision": "APPROVE"}},
        )

        item = client.get("/api/v1/cases", headers=auth(login("a@a.com"))).json()["items"][0]
        assert item["degraded_components"] == ["sector_context"]
        assert item["lifecycle_status"] == "READY_FOR_REVIEW"

    def test_no_case_ever_reports_unknown(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        for status in ("PENDING", "RUNNING", "RETRYING", "PAUSED", "COMPLETED",
                       "FAILED", "REJECTED", "NONSENSE"):
            seed_case(tenant, status=status)

        body = client.get("/api/v1/cases?limit=100", headers=auth(login("a@a.com"))).json()
        assert body["total"] == 8
        for item in body["items"]:
            assert item["lifecycle_status"] != "UNKNOWN"
            assert item["lifecycle_status"] is not None


# ---------------------------------------------------------------------------
# 6. audit chain integrity
# ---------------------------------------------------------------------------

class TestAuditChainStaysIntact:
    def test_reading_events_does_not_break_the_chain(self):
        user_id, tenant = make_user("admin@a.com", role="ORG_ADMIN")
        case_id = seed_case(tenant)
        seed_audit_event(tenant, user_id, action="CASE_CREATED", case_id=case_id)
        seed_audit_event(tenant, user_id, action="ANALYSIS_STARTED", case_id=case_id)
        token = login("admin@a.com")

        client.get("/api/v1/audit/events", headers=auth(token))
        verify = client.get("/api/v1/audit/verify", headers=auth(token)).json()
        assert verify["chain"]["status"] == "valid"

    def test_audit_logs_remain_append_only(self):
        """The read path must not have relaxed the append-only triggers."""
        user_id, tenant = make_user("admin@a.com", role="ORG_ADMIN")
        seed_audit_event(tenant, user_id, action="CASE_CREATED")

        import sqlite3
        conn = get_sqlite_connection()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE audit_logs SET action = 'TAMPERED' WHERE tenant_id = ?", (tenant,))
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM audit_logs WHERE tenant_id = ?", (tenant,))
        finally:
            conn.close()

    def test_events_carry_their_hash_links(self):
        user_id, tenant = make_user("admin@a.com", role="ORG_ADMIN")
        seed_audit_event(tenant, user_id, action="CASE_CREATED")

        body = client.get("/api/v1/audit/events", headers=auth(login("admin@a.com"))).json()
        event = body["items"][0]
        assert len(event["current_hash"]) == 64
        assert len(event["previous_hash"]) == 64
        assert event["sequence_number"] >= 1

    def test_action_filter_narrows_the_result(self):
        user_id, tenant = make_user("admin@a.com", role="ORG_ADMIN")
        seed_audit_event(tenant, user_id, action="CASE_CREATED")
        seed_audit_event(tenant, user_id, action="DECISION_RECORDED")

        body = client.get(
            "/api/v1/audit/events?action=DECISION_RECORDED", headers=auth(login("admin@a.com"))
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "DECISION_RECORDED"


# ---------------------------------------------------------------------------
# 7. no internal handles leak
# ---------------------------------------------------------------------------

class TestNoInternalLeakage:
    def test_document_listing_never_returns_a_storage_path(self):
        _, tenant = make_user("a@a.com", role="CREDIT_ANALYST")
        case_id = seed_case(tenant)
        conn = get_sqlite_connection()
        conn.execute(
            "INSERT INTO case_documents (id, case_id, tenant_id, filename, storage_path, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, case_id, tenant, "balance_sheet.pdf",
             "supabase://credent-documents/secret/path.pdf", "COMPLETED"),
        )
        conn.commit()
        conn.close()

        body = client.get(
            f"/api/v1/cases/{case_id}/documents", headers=auth(login("a@a.com"))
        ).json()
        assert body["items"][0]["filename"] == "balance_sheet.pdf"
        assert "storage_path" not in body["items"][0]
        assert "secret/path.pdf" not in json.dumps(body)
