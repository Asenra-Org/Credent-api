"""Phase 4 - the platform operations console.

Three properties matter more than the feature surface, and each has its own
class below:

  * **Only a SUPER_ADMIN reaches these endpoints.** Every other role, and every
    unauthenticated caller, is refused.
  * **A platform operator still cannot read credit content.** Operating the
    platform and underwriting a loan are different jobs. The operational case
    view exposes processing state and timestamps; borrower names, amounts,
    decisions, CAM contents and documents stay out of it, and ``GET /cases``
    continues to refuse SUPER_ADMIN outright.
  * **No credential material is ever returned.** Not a password hash, not an MFA
    secret, not a session token, not an API key, not a database URL.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database.database import get_sqlite_connection, init_db
from app.main import app
from app.security.auth_service import hash_password

client = TestClient(app)

PLATFORM_GET_ENDPOINTS = [
    "/api/v1/platform/overview",
    "/api/v1/platform/case-trend",
    "/api/v1/platform/status-distribution",
    "/api/v1/platform/organizations",
    "/api/v1/platform/users",
    "/api/v1/platform/cases",
    "/api/v1/platform/health",
    "/api/v1/platform/ai-operations",
    "/api/v1/platform/usage",
    "/api/v1/platform/configuration",
]


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
    c.execute("DELETE FROM organizations WHERE name LIKE 'PhaseFour%'")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_db():
    _truncate()
    yield
    _truncate()


def make_user(email, role, password="password123", tenant_id=None, org_name=None):
    conn = get_sqlite_connection()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    tenant_id = tenant_id or str(uuid.uuid4())
    if org_name:
        c.execute("INSERT OR REPLACE INTO organizations (id, name) VALUES (?, ?)", (tenant_id, org_name))
    c.execute(
        "INSERT INTO users (id, email, password_hash, is_active, is_locked, failed_login_count, "
        "mfa_enabled) VALUES (?, ?, ?, 1, 0, 0, 0)",
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


def super_admin_token():
    make_user("operator@cresem.io", "SUPER_ADMIN", org_name="PhaseFour Platform")
    return login("operator@cresem.io")


def seed_case(tenant_id, borrower="Confidential Borrower Ltd", status="COMPLETED"):
    case_id = uuid.uuid4().hex
    conn = get_sqlite_connection()
    conn.execute(
        """INSERT INTO loan_cases (case_id, status, current_step, institution_id, input_data,
           result_data, borrower_name, requested_amount, facility_type, decision)
           VALUES (?, ?, 'done', ?, '{}', ?, ?, ?, ?, ?)""",
        (case_id, status, tenant_id,
         json.dumps({"combined_decision": {"decision": "APPROVE"}}),
         borrower, 99000000, "Term Loan", "APPROVE"),
    )
    conn.commit()
    conn.close()
    return case_id


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

class TestOnlySuperAdminReachesThePlatformConsole:
    @pytest.mark.parametrize("path", PLATFORM_GET_ENDPOINTS)
    def test_anonymous_is_refused(self, path):
        assert client.get(path).status_code in (401, 403)

    @pytest.mark.parametrize("path", PLATFORM_GET_ENDPOINTS)
    def test_forged_token_is_refused(self, path):
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401

    @pytest.mark.parametrize("role", ["ORG_ADMIN", "UNDERWRITING_MANAGER", "CREDIT_ANALYST", "VIEWER"])
    def test_every_tenant_role_is_refused(self, role):
        make_user("tenant@bank.com", role)
        token = login("tenant@bank.com")
        for path in PLATFORM_GET_ENDPOINTS:
            assert client.get(path, headers=auth(token)).status_code == 403, path

    def test_super_admin_is_allowed(self):
        token = super_admin_token()
        for path in PLATFORM_GET_ENDPOINTS:
            assert client.get(path, headers=auth(token)).status_code == 200, path

    @pytest.mark.parametrize("role", ["ORG_ADMIN", "UNDERWRITING_MANAGER", "CREDIT_ANALYST"])
    def test_tenant_roles_cannot_create_organizations(self, role):
        make_user("tenant@bank.com", role)
        res = client.post(
            "/api/v1/platform/organizations",
            headers=auth(login("tenant@bank.com")),
            json={"name": "PhaseFour Rogue Bank"},
        )
        assert res.status_code == 403

    def test_tenant_roles_cannot_disable_an_organization(self):
        _, tenant = make_user("admin@bank.com", "ORG_ADMIN")
        res = client.patch(
            f"/api/v1/platform/organizations/{tenant}",
            headers=auth(login("admin@bank.com")),
            json={"is_active": False},
        )
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

class TestPlatformOperatorCannotReadCreditContent:
    def test_super_admin_is_still_refused_the_tenant_case_api(self):
        """The Phase 2 boundary must survive Phase 4."""
        token = super_admin_token()
        assert client.get("/api/v1/cases", headers=auth(token)).status_code == 403

    def test_operational_case_view_omits_borrower_identity(self):
        token = super_admin_token()
        _, tenant = make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")
        seed_case(tenant, borrower="Confidential Borrower Ltd")

        res = client.get("/api/v1/platform/cases", headers=auth(token))
        assert res.status_code == 200
        body = res.text
        assert "Confidential Borrower Ltd" not in body
        assert "borrower_name" not in body

    def test_operational_case_view_omits_amounts_and_decisions(self):
        token = super_admin_token()
        _, tenant = make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")
        seed_case(tenant)

        item = client.get("/api/v1/platform/cases", headers=auth(token)).json()["items"][0]
        for forbidden in (
            "borrower_name", "requested_amount", "facility_type", "decision",
            "result_data", "input_data", "cam_report", "documents",
        ):
            assert forbidden not in item, f"{forbidden} must not reach a platform operator"

    def test_operational_case_view_returns_processing_state(self):
        """What an operator legitimately needs is present."""
        token = super_admin_token()
        _, tenant = make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")
        seed_case(tenant, status="FAILED")

        item = client.get("/api/v1/platform/cases", headers=auth(token)).json()["items"][0]
        assert item["status"] == "FAILED"
        assert item["organization_id"] == tenant
        assert "created_at" in item and "updated_at" in item

    def test_organization_detail_reports_counts_not_case_contents(self):
        token = super_admin_token()
        _, tenant = make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")
        seed_case(tenant, borrower="Confidential Borrower Ltd")

        res = client.get(f"/api/v1/platform/organizations/{tenant}", headers=auth(token))
        assert res.status_code == 200
        assert res.json()["organization"]["case_count"] == 1
        assert "Confidential Borrower Ltd" not in res.text


# ---------------------------------------------------------------------------
# Credential exposure
# ---------------------------------------------------------------------------

class TestNoCredentialMaterialIsReturned:
    def test_user_listing_never_returns_a_password_hash(self):
        token = super_admin_token()
        make_user("analyst@bank.com", "CREDIT_ANALYST", password="a-very-secret-password")

        body = client.get("/api/v1/platform/users", headers=auth(token)).text
        assert "password_hash" not in body
        assert "$2b$" not in body and "$argon2" not in body
        assert "a-very-secret-password" not in body

    def test_user_listing_never_returns_an_mfa_secret(self):
        token = super_admin_token()
        user_id, _ = make_user("analyst@bank.com", "CREDIT_ANALYST")
        conn = get_sqlite_connection()
        conn.execute("UPDATE users SET mfa_secret = ?, mfa_enabled = 1 WHERE id = ?",
                     ("JBSWY3DPEHPK3PXP", user_id))
        conn.commit()
        conn.close()

        body = client.get("/api/v1/platform/users", headers=auth(token)).text
        assert "JBSWY3DPEHPK3PXP" not in body
        assert "mfa_secret" not in body
        # Posture is still reported, which is the useful part.
        assert '"mfa_enabled": true' in body.replace("'", '"').lower() or "mfa_enabled" in body

    def test_organization_detail_never_returns_credentials(self):
        token = super_admin_token()
        _, tenant = make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")
        body = client.get(f"/api/v1/platform/organizations/{tenant}", headers=auth(token)).text
        assert "password_hash" not in body and "mfa_secret" not in body

    def test_configuration_reports_posture_not_secret_values(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_SUPER_SECRET_VALUE")
        monkeypatch.setenv("AUDIT_HMAC_SECRET", "hmac_SUPER_SECRET_VALUE")
        token = super_admin_token()

        body = client.get("/api/v1/platform/configuration", headers=auth(token)).text
        assert "gsk_SUPER_SECRET_VALUE" not in body
        assert "hmac_SUPER_SECRET_VALUE" not in body
        assert "configured" in body

    def test_configuration_is_not_editable(self):
        token = super_admin_token()
        assert client.get("/api/v1/platform/configuration", headers=auth(token)).json()["editable"] is False


# ---------------------------------------------------------------------------
# Metrics honesty
# ---------------------------------------------------------------------------

class TestUnmeasuredMetricsAreNotFabricated:
    def test_overview_reports_unmeasurable_metrics_as_not_measured(self):
        token = super_admin_token()
        metrics = {m["metric"]: m for m in client.get("/api/v1/platform/overview", headers=auth(token)).json()["metrics"]}

        for key in ("platform_ai_calls", "ai_cost", "average_processing_time", "system_error_rate"):
            assert metrics[key]["measured"] is False
            assert metrics[key]["value"] is None, f"{key} must be null, never a fabricated 0"
            assert metrics[key]["requires"], f"{key} must explain what telemetry is missing"

    def test_measurable_counts_are_real(self):
        token = super_admin_token()
        _, tenant = make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")
        seed_case(tenant)
        seed_case(tenant)

        metrics = {m["metric"]: m for m in client.get("/api/v1/platform/overview", headers=auth(token)).json()["metrics"]}
        assert metrics["total_cases"]["measured"] is True
        assert metrics["total_cases"]["value"] == 2

    def test_ai_operations_does_not_invent_request_counts(self):
        token = super_admin_token()
        body = client.get("/api/v1/platform/ai-operations", headers=auth(token)).json()
        unmeasured = {m["metric"] for m in body["unmeasured"]}
        assert {"requests", "average_latency", "token_usage", "failover_events"} <= unmeasured
        for m in body["unmeasured"]:
            assert m["value"] is None

    def test_usage_never_invents_a_cost_figure(self):
        token = super_admin_token()
        body = client.get("/api/v1/platform/usage", headers=auth(token)).json()
        cost = next(m for m in body["unmeasured"] if m["metric"] == "estimated_cost")
        assert cost["value"] is None
        assert cost["measured"] is False


# ---------------------------------------------------------------------------
# Organization lifecycle
# ---------------------------------------------------------------------------

class TestOrganizationLifecycle:
    def test_create_organization(self):
        token = super_admin_token()
        res = client.post(
            "/api/v1/platform/organizations",
            headers=auth(token),
            json={"name": "PhaseFour Meridian Bank"},
        )
        assert res.status_code == 201, res.text
        assert res.json()["organization"]["name"] == "PhaseFour Meridian Bank"

    def test_duplicate_organization_name_is_refused(self):
        token = super_admin_token()
        payload = {"name": "PhaseFour Duplicate Bank"}
        assert client.post("/api/v1/platform/organizations", headers=auth(token), json=payload).status_code == 201
        assert client.post("/api/v1/platform/organizations", headers=auth(token), json=payload).status_code == 409

    def test_admin_provisioning_issues_an_invitation_not_a_password(self):
        token = super_admin_token()
        res = client.post(
            "/api/v1/platform/organizations",
            headers=auth(token),
            json={"name": "PhaseFour Invited Bank", "admin_email": "newadmin@invited.com"},
        )
        assert res.status_code == 201, res.text
        invitation = res.json()["invitation"]
        assert invitation["role"] == "ORG_ADMIN"
        assert invitation["token"], "an invitation token must be issued"
        # Critically: no password of any kind is returned.
        assert "password" not in res.text.lower()

    def test_only_the_invitation_hash_is_stored(self):
        token = super_admin_token()
        res = client.post(
            "/api/v1/platform/organizations",
            headers=auth(token),
            json={"name": "PhaseFour Hashed Bank", "admin_email": "hashed@invited.com"},
        )
        raw = res.json()["invitation"]["token"]
        conn = get_sqlite_connection()
        try:
            row = conn.execute(
                "SELECT token_hash FROM invitations WHERE email = ?", ("hashed@invited.com",)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] != raw, "the raw invitation token must never be stored"

    def test_disable_and_enable_an_organization(self):
        token = super_admin_token()
        org_id = client.post(
            "/api/v1/platform/organizations", headers=auth(token),
            json={"name": "PhaseFour Toggle Bank"},
        ).json()["organization"]["id"]

        assert client.patch(f"/api/v1/platform/organizations/{org_id}", headers=auth(token),
                            json={"is_active": False}).status_code == 200
        listed = client.get("/api/v1/platform/organizations?search=PhaseFour Toggle",
                            headers=auth(token)).json()["items"]
        assert listed[0]["is_active"] is False

        client.patch(f"/api/v1/platform/organizations/{org_id}", headers=auth(token),
                     json={"is_active": True})
        listed = client.get("/api/v1/platform/organizations?search=PhaseFour Toggle",
                            headers=auth(token)).json()["items"]
        assert listed[0]["is_active"] is True

    def test_unknown_organization_is_404(self):
        token = super_admin_token()
        assert client.get(f"/api/v1/platform/organizations/{uuid.uuid4()}",
                          headers=auth(token)).status_code == 404


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class TestPlatformUsers:
    def test_lists_users_with_their_organization_and_role(self):
        token = super_admin_token()
        make_user("analyst@bank.com", "CREDIT_ANALYST", org_name="PhaseFour Client Bank")

        items = client.get("/api/v1/platform/users", headers=auth(token)).json()["items"]
        analyst = next(u for u in items if u["email"] == "analyst@bank.com")
        assert analyst["role"] == "CREDIT_ANALYST"
        assert analyst["organization_name"] == "PhaseFour Client Bank"

    def test_last_login_is_recorded_on_sign_in(self):
        token = super_admin_token()
        make_user("analyst@bank.com", "CREDIT_ANALYST")
        login("analyst@bank.com")

        items = client.get("/api/v1/platform/users", headers=auth(token)).json()["items"]
        analyst = next(u for u in items if u["email"] == "analyst@bank.com")
        assert analyst["last_login_at"] is not None

    def test_never_signed_in_reads_as_null_not_a_fake_date(self):
        token = super_admin_token()
        make_user("dormant@bank.com", "CREDIT_ANALYST")
        items = client.get("/api/v1/platform/users", headers=auth(token)).json()["items"]
        dormant = next(u for u in items if u["email"] == "dormant@bank.com")
        assert dormant["last_login_at"] is None

    def test_filter_by_role(self):
        token = super_admin_token()
        make_user("analyst@bank.com", "CREDIT_ANALYST")
        make_user("manager@bank.com", "UNDERWRITING_MANAGER")

        items = client.get("/api/v1/platform/users?role=CREDIT_ANALYST", headers=auth(token)).json()["items"]
        assert all(u["role"] == "CREDIT_ANALYST" for u in items)

    def test_unknown_role_filter_is_rejected(self):
        token = super_admin_token()
        assert client.get("/api/v1/platform/users?role=ROOT", headers=auth(token)).status_code == 400

    def test_deactivate_a_user(self):
        token = super_admin_token()
        user_id, _ = make_user("analyst@bank.com", "CREDIT_ANALYST")
        assert client.patch(f"/api/v1/platform/users/{user_id}/status", headers=auth(token),
                            json={"is_active": False}).status_code == 200

        items = client.get("/api/v1/platform/users", headers=auth(token)).json()["items"]
        analyst = next(u for u in items if u["email"] == "analyst@bank.com")
        assert analyst["is_active"] is False


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestSystemHealth:
    def test_reports_every_component(self):
        token = super_admin_token()
        body = client.get("/api/v1/platform/health", headers=auth(token)).json()
        names = {c["component"] for c in body["components"]}
        assert {"database", "authentication", "llm_provider", "storage", "queue", "api"} <= names

    def test_database_health_is_actually_measured(self):
        token = super_admin_token()
        body = client.get("/api/v1/platform/health", headers=auth(token)).json()
        db = next(c for c in body["components"] if c["component"] == "database")
        assert db["state"] == "operational"
        assert isinstance(db["response_ms"], (int, float))

    def test_health_never_exposes_the_provider_key(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_SECRET_HEALTH_VALUE")
        token = super_admin_token()
        assert "gsk_SECRET_HEALTH_VALUE" not in client.get("/api/v1/platform/health", headers=auth(token)).text
