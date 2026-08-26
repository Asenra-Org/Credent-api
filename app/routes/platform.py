# =============================================================================
# CRESEM - Platform operations console (SUPER_ADMIN)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
"""Platform-operator endpoints.

A SUPER_ADMIN operates CRESEM; they are not an employee of any customer
organization. That distinction is the load-bearing rule in this module:

  * **Operational metadata is in scope.** How many cases an organization has,
    whether they completed, when they were last touched, whether the pipeline is
    failing - an operator needs all of it to run the platform.

  * **Credit content is not.** Borrower names, requested amounts, financial
    figures, CAM contents, documents and raw model output are never selected by
    any query here. ``GET /cases`` continues to refuse SUPER_ADMIN outright, and
    nothing in this module is a way around that.

  * **Secrets are never returned.** No password hash, MFA secret, session token,
    invitation token, API key or database URL is read by any query in this
    module. The configuration endpoint reports whether a value is *configured*,
    never what it is.

Metrics the platform does not measure are returned as an explicit
``not_measured`` descriptor naming the telemetry that is missing, rather than as
a zero. A zero on an operations console reads as a measurement.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from app.database.auth_db import get_auth_connection, uses_postgres
from app.database.database import get_app_connection
from app.security.dependencies import get_current_user_and_session, require_role
from app.security.rate_limit_dependency import rate_limit

router = APIRouter(prefix="/api/v1/platform", tags=["Platform"])

# Every route in this module is platform-operator only.
SUPER_ADMIN_ONLY = require_role(["SUPER_ADMIN"])

# Roles an operator may assign. Deliberately closed: arbitrary role strings must
# never reach tenant_memberships.
ASSIGNABLE_ROLES = ("ORG_ADMIN", "CREDIT_ANALYST", "UNDERWRITING_MANAGER", "VIEWER")


def not_measured(metric: str, requires: str) -> Dict[str, Any]:
    """A metric the platform cannot currently produce.

    Returning ``value: None`` plus the reason lets the console render an
    explicit NOT MEASURED state. Returning 0 here would be a fabricated
    measurement, which on a credit platform is worse than an absent one.
    """
    return {"metric": metric, "value": None, "measured": False, "requires": requires}


def measured(metric: str, value: Any, unit: Optional[str] = None) -> Dict[str, Any]:
    return {"metric": metric, "value": value, "measured": True, "unit": unit}


def _scalar(cursor, sql: str, params: tuple = ()) -> int:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# =============================================================================
# Overview
# =============================================================================

@router.get("/overview", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def platform_overview():
    """Executive platform KPIs, counted from real rows."""
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    auth = get_auth_connection()
    try:
        ac = auth.cursor()
        total_orgs = _scalar(ac, "SELECT COUNT(*) FROM organizations")
        active_orgs = _scalar(ac, "SELECT COUNT(*) FROM organizations WHERE is_active = 1")
        total_users = _scalar(ac, "SELECT COUNT(*) FROM users")
        active_users = _scalar(ac, "SELECT COUNT(*) FROM users WHERE is_active = 1")
    finally:
        auth.close()

    conn = get_app_connection()
    try:
        c = conn.cursor()
        total_cases = _scalar(c, "SELECT COUNT(*) FROM loan_cases")
        cases_this_month = _scalar(
            c, "SELECT COUNT(*) FROM loan_cases WHERE created_at >= ?", (month_start,)
        )
        completed = _scalar(c, "SELECT COUNT(*) FROM loan_cases WHERE status = 'COMPLETED'")
        failed_cases = _scalar(c, "SELECT COUNT(*) FROM loan_cases WHERE status IN ('FAILED', 'REJECTED')")
        # An appraisal the safety gate refused. Distinct from a failed case:
        # the run finished but produced no usable credit decision.
        incomplete = _scalar(
            c, "SELECT COUNT(*) FROM appraisal_records WHERE decision_allowed = 0"
        )
        total_appraisals = _scalar(c, "SELECT COUNT(*) FROM appraisal_records")
    finally:
        conn.close()

    return {
        "status": "success",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": [
            measured("total_organizations", total_orgs),
            measured("active_organizations", active_orgs),
            measured("total_users", total_users),
            measured("active_users", active_users),
            measured("total_cases", total_cases),
            measured("cases_this_month", cases_this_month),
            measured("completed_cases", completed),
            measured("failed_cases", failed_cases),
            measured("total_appraisals", total_appraisals),
            measured("analysis_incomplete_appraisals", incomplete),
            not_measured(
                "platform_ai_calls",
                "Per-call LLM telemetry. Provenance is recorded per appraisal, "
                "not per model call, so request counts cannot be derived.",
            ),
            not_measured(
                "ai_cost",
                "Per-call token accounting plus provider pricing. Neither is captured.",
            ),
            not_measured(
                "average_processing_time",
                "Pipeline start and finish timestamps per case. Only created_at "
                "and updated_at exist, and updated_at moves on every write.",
            ),
            not_measured(
                "system_error_rate",
                "Request-level outcome counters. Errors are logged, not aggregated.",
            ),
        ],
    }


@router.get("/case-trend", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def case_trend(days: int = Query(default=30, ge=1, le=365)):
    """Cases created per day, counted from loan_cases.created_at."""
    conn = get_app_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT substr(created_at, 1, 10) AS day, COUNT(*)
               FROM loan_cases
               WHERE created_at IS NOT NULL
               GROUP BY day ORDER BY day DESC LIMIT ?""",
            (days,),
        )
        rows = [{"date": r[0], "cases": int(r[1])} for r in c.fetchall()]
    finally:
        conn.close()

    rows.reverse()
    return {"status": "success", "period_days": days, "unit": "cases per day", "items": rows}


@router.get("/status-distribution", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def status_distribution():
    """Appraisal analysis-status distribution, straight from the gate's own column."""
    conn = get_app_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT COALESCE(analysis_status, 'NOT_RECORDED'), COUNT(*)
               FROM appraisal_records GROUP BY 1 ORDER BY 2 DESC"""
        )
        rows = [{"analysis_status": r[0], "count": int(r[1])} for r in c.fetchall()]
    finally:
        conn.close()
    return {"status": "success", "items": rows}


# =============================================================================
# Organizations
# =============================================================================

class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    admin_email: Optional[EmailStr] = None


class UpdateOrganizationRequest(BaseModel):
    is_active: bool


def _org_activity(org_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Case counts and last activity per organization.

    Counts and timestamps only - no borrower, amount or decision content.
    """
    if not org_ids:
        return {}
    placeholders = ",".join("?" for _ in org_ids)
    conn = get_app_connection()
    try:
        c = conn.cursor()
        c.execute(
            f"""SELECT institution_id, COUNT(*), MAX(updated_at)
                FROM loan_cases WHERE institution_id IN ({placeholders})
                GROUP BY institution_id""",
            org_ids,
        )
        return {
            r[0]: {"case_count": int(r[1]), "last_activity": r[2]} for r in c.fetchall()
        }
    finally:
        conn.close()


@router.get("/organizations", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def list_organizations(
    search: Optional[str] = Query(default=None, max_length=200),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Organizations enriched with user counts, case counts and last activity."""
    where, params = [], []
    if search:
        where.append("LOWER(o.name) LIKE ?")
        params.append(f"%{search.lower()}%")
    if is_active is not None:
        where.append("o.is_active = ?")
        params.append(1 if is_active else 0)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    auth = get_auth_connection()
    try:
        ac = auth.cursor()
        ac.execute(f"SELECT COUNT(*) FROM organizations o{where_sql}", params)
        total = int(ac.fetchone()[0])

        ac.execute(
            f"""SELECT o.id, o.name, o.is_active, o.created_at,
                       (SELECT COUNT(*) FROM tenant_memberships tm WHERE tm.tenant_id = o.id)
                FROM organizations o{where_sql}
                ORDER BY o.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = ac.fetchall()
    finally:
        auth.close()

    org_ids = [r[0] for r in rows]
    activity = _org_activity(org_ids)

    items = []
    for r in rows:
        act = activity.get(r[0], {})
        items.append({
            "id": r[0],
            "name": r[1],
            "is_active": bool(r[2]),
            "created_at": r[3],
            "user_count": int(r[4]),
            "case_count": act.get("case_count", 0),
            "last_activity": act.get("last_activity"),
            # Usage requires per-call telemetry that does not exist yet.
            "usage_measured": False,
        })

    return {"status": "success", "items": items, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/organizations",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))],
)
async def create_organization(
    req: CreateOrganizationRequest,
    user_ctx: dict = Depends(get_current_user_and_session),
):
    """Provision a new organization, optionally with its first ORG_ADMIN.

    Admin provisioning issues a single-use invitation token through the existing
    invitations table. No password is generated, transmitted or displayed: the
    invitee sets their own credential through the normal authentication flow.
    """
    import hashlib
    import secrets
    import uuid as _uuid
    from datetime import timedelta

    from app.security.auth_service import hash_password

    org_id = str(_uuid.uuid4())
    invitation = None

    auth = get_auth_connection()
    try:
        ac = auth.cursor()

        ac.execute("SELECT id FROM organizations WHERE LOWER(name) = ?", (req.name.strip().lower(),))
        if ac.fetchone():
            raise HTTPException(status_code=409, detail="An organization with that name already exists")

        ac.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (org_id, req.name.strip()))

        if req.admin_email:
            ac.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (req.admin_email,))
            existing = ac.fetchone()
            if existing:
                admin_user_id = existing[0]
            else:
                admin_user_id = str(_uuid.uuid4())
                # The account is created with an unusable random credential and
                # is claimed through the invitation token. The value below is
                # never returned, logged or displayed.
                ac.execute(
                    "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                    (admin_user_id, req.admin_email, hash_password(secrets.token_urlsafe(32))),
                )

            ac.execute(
                "SELECT user_id FROM tenant_memberships WHERE user_id = ? AND tenant_id = ?",
                (admin_user_id, org_id),
            )
            if not ac.fetchone():
                ac.execute(
                    "INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, 'ORG_ADMIN')",
                    (admin_user_id, org_id),
                )

            raw_token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            ac.execute(
                "INSERT INTO invitations (id, email, organization_id, role, token_hash, expires_at, created_by) "
                "VALUES (?, ?, ?, 'ORG_ADMIN', ?, ?, ?)",
                (
                    str(_uuid.uuid4()), req.admin_email, org_id,
                    hashlib.sha256(raw_token.encode()).hexdigest(), expires_at, user_ctx["user_id"],
                ),
            )
            invitation = {
                "email": req.admin_email,
                "role": "ORG_ADMIN",
                "expires_at": expires_at,
                # Shown once to the operator so they can deliver it out of band.
                # Only the hash is stored.
                "token": raw_token,
            }

        auth.commit()
    except HTTPException:
        auth.rollback()
        raise
    except Exception:
        auth.rollback()
        raise HTTPException(status_code=500, detail="Organization could not be created")
    finally:
        auth.close()

    return {
        "status": "success",
        "organization": {"id": org_id, "name": req.name.strip(), "is_active": True},
        "invitation": invitation,
    }


@router.get(
    "/organizations/{org_id}",
    dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))],
)
async def organization_detail(org_id: str):
    """One organization: profile, membership, and operational case counts."""
    auth = get_auth_connection()
    try:
        ac = auth.cursor()
        ac.execute("SELECT id, name, is_active, created_at FROM organizations WHERE id = ?", (org_id,))
        row = ac.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")

        ac.execute(
            """SELECT u.id, u.email, u.is_active, u.created_at, u.last_login_at,
                      tm.role, tm.is_active, u.mfa_enabled
               FROM users u JOIN tenant_memberships tm ON u.id = tm.user_id
               WHERE tm.tenant_id = ? ORDER BY u.created_at DESC""",
            (org_id,),
        )
        users = [
            {
                "user_id": u[0], "email": u[1], "is_active": bool(u[2]),
                "created_at": u[3], "last_login_at": u[4], "role": u[5],
                "membership_active": bool(u[6]), "mfa_enabled": bool(u[7]),
            }
            for u in ac.fetchall()
        ]
    finally:
        auth.close()

    conn = get_app_connection()
    try:
        c = conn.cursor()
        case_count = _scalar(c, "SELECT COUNT(*) FROM loan_cases WHERE institution_id = ?", (org_id,))
        c.execute(
            """SELECT COALESCE(status, 'NOT_RECORDED'), COUNT(*) FROM loan_cases
               WHERE institution_id = ? GROUP BY 1""",
            (org_id,),
        )
        by_status = [{"status": r[0], "count": int(r[1])} for r in c.fetchall()]
        c.execute("SELECT MAX(updated_at) FROM loan_cases WHERE institution_id = ?", (org_id,))
        last_activity = c.fetchone()[0]
    finally:
        conn.close()

    return {
        "status": "success",
        "organization": {
            "id": row[0], "name": row[1], "is_active": bool(row[2]), "created_at": row[3],
            "user_count": len(users), "case_count": case_count, "last_activity": last_activity,
        },
        "users": users,
        # Counts by processing state only. No borrower or decision content.
        "case_status_counts": by_status,
        "usage": not_measured(
            "organization_usage",
            "Per-call LLM telemetry keyed by tenant. Not captured.",
        ),
    }


@router.patch(
    "/organizations/{org_id}",
    dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))],
)
async def set_organization_status(org_id: str, req: UpdateOrganizationRequest):
    """Enable or disable an organization."""
    auth = get_auth_connection()
    try:
        ac = auth.cursor()
        ac.execute("UPDATE organizations SET is_active = ? WHERE id = ?", (1 if req.is_active else 0, org_id))
        if ac.rowcount == 0:
            raise HTTPException(status_code=404, detail="Organization not found")
        auth.commit()
    finally:
        auth.close()
    return {"status": "success", "organization_id": org_id, "is_active": req.is_active}


# =============================================================================
# Users
# =============================================================================

@router.get("/users", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def list_platform_users(
    search: Optional[str] = Query(default=None, max_length=200),
    organization_id: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Every user on the platform.

    The select list is explicit and deliberately excludes password_hash and
    mfa_secret. ``mfa_enabled`` is returned as a boolean posture indicator; the
    secret behind it never leaves the database.
    """
    if role and role not in ASSIGNABLE_ROLES and role != "SUPER_ADMIN":
        raise HTTPException(status_code=400, detail=f"Unknown role. Expected one of: {ASSIGNABLE_ROLES}")

    where, params = [], []
    if search:
        where.append("LOWER(u.email) LIKE ?")
        params.append(f"%{search.lower()}%")
    if organization_id:
        where.append("tm.tenant_id = ?")
        params.append(organization_id)
    if role:
        where.append("tm.role = ?")
        params.append(role)
    if is_active is not None:
        where.append("u.is_active = ?")
        params.append(1 if is_active else 0)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    auth = get_auth_connection()
    try:
        ac = auth.cursor()
        ac.execute(
            f"""SELECT COUNT(*) FROM users u
                LEFT JOIN tenant_memberships tm ON u.id = tm.user_id{where_sql}""",
            params,
        )
        total = int(ac.fetchone()[0])

        ac.execute(
            f"""SELECT u.id, u.email, u.is_active, u.is_locked, u.created_at,
                       u.last_login_at, u.mfa_enabled, tm.role, tm.tenant_id,
                       tm.is_active, o.name
                FROM users u
                LEFT JOIN tenant_memberships tm ON u.id = tm.user_id
                LEFT JOIN organizations o ON tm.tenant_id = o.id{where_sql}
                ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        items = [
            {
                "user_id": r[0], "email": r[1], "is_active": bool(r[2]),
                "is_locked": bool(r[3]), "created_at": r[4], "last_login_at": r[5],
                "mfa_enabled": bool(r[6]), "role": r[7], "organization_id": r[8],
                "membership_active": bool(r[9]) if r[9] is not None else None,
                "organization_name": r[10],
            }
            for r in ac.fetchall()
        ]
    finally:
        auth.close()

    return {"status": "success", "items": items, "total": total, "limit": limit, "offset": offset}


class UpdateUserStatusRequest(BaseModel):
    is_active: bool


@router.patch(
    "/users/{user_id}/status",
    dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))],
)
async def set_user_status(user_id: str, req: UpdateUserStatusRequest):
    """Activate or deactivate any account on the platform."""
    auth = get_auth_connection()
    try:
        ac = auth.cursor()
        ac.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if req.is_active else 0, user_id))
        if ac.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        auth.commit()
    finally:
        auth.close()
    return {"status": "success", "user_id": user_id, "is_active": req.is_active}


# =============================================================================
# Cases - operational metadata only
# =============================================================================

@router.get("/cases", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def platform_cases(
    organization_id: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Cases across all organizations, as operational records only.

    This is NOT a way for a platform operator to read credit files. The select
    list carries the case identifier, its owning organization, processing state
    and timestamps. It deliberately omits borrower_name, requested_amount,
    facility_type, decision, result_data, documents and every CAM field.
    Tenant-scoped credit content stays behind ``GET /cases``, which continues to
    refuse SUPER_ADMIN.
    """
    where, params = [], []
    if organization_id:
        where.append("institution_id = ?")
        params.append(organization_id)
    if status_filter:
        where.append("status = ?")
        params.append(status_filter)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_app_connection()
    try:
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) FROM loan_cases{where_sql}", params)
        total = int(c.fetchone()[0])
        c.execute(
            f"""SELECT case_id, institution_id, status, current_step, analysis_status,
                       decision_allowed, created_at, updated_at,
                       CASE WHEN error_message IS NULL THEN 0 ELSE 1 END
                FROM loan_cases{where_sql}
                ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        items = [
            {
                "case_id": r[0], "organization_id": r[1], "status": r[2],
                "current_step": r[3], "analysis_status": r[4],
                "decision_allowed": None if r[5] is None else bool(r[5]),
                "created_at": r[6], "updated_at": r[7], "has_error": bool(r[8]),
            }
            for r in c.fetchall()
        ]
    finally:
        conn.close()

    return {
        "status": "success",
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "scope": "operational_metadata_only",
    }


# =============================================================================
# System health
# =============================================================================

@router.get("/health", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def system_health():
    """Component health, measured now rather than reported from a cache."""
    components = []

    # --- Application database ---
    started = time.perf_counter()
    try:
        conn = get_app_connection()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        components.append({
            "component": "database",
            "state": "operational",
            "detail": "Supabase (primary)" if os.getenv("SUPABASE_URL") else "SQLite (development)",
            "response_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    except Exception as exc:
        components.append({
            "component": "database", "state": "failed",
            "detail": type(exc).__name__, "response_ms": None,
        })

    # --- Identity store ---
    started = time.perf_counter()
    try:
        auth = get_auth_connection()
        try:
            auth.cursor().execute("SELECT 1")
        finally:
            auth.close()
        components.append({
            "component": "authentication",
            "state": "operational",
            "detail": "Postgres identity store" if uses_postgres() else "SQLite identity store (development)",
            "response_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    except Exception as exc:
        components.append({
            "component": "authentication", "state": "failed",
            "detail": type(exc).__name__, "response_ms": None,
        })

    # --- LLM provider. Configuration presence only; no key value, and no live
    # probe (a health check must not spend model quota).
    from app.core.llm import active_provider as _active_provider

    _llm = _active_provider()
    components.append({
        "component": "llm_provider",
        "state": "configured" if _llm["provider"] else "not_configured",
        # Names the provider that is live, not merely one whose key happens to
        # be present in the environment.
        "detail": (
            f"{_llm['provider']} / {_llm['primary_model']}" if _llm["provider"] else "not set"
        ),
        "response_ms": None,
    })

    # --- Object storage ---
    components.append({
        "component": "storage",
        "state": "configured" if os.getenv("SUPABASE_URL") else "not_configured",
        "detail": os.getenv("SUPABASE_STORAGE_BUCKET", "not set"),
        "response_ms": None,
    })

    # --- Queue / worker ---
    use_celery = (os.getenv("USE_CELERY", "false") or "").strip().lower() == "true"
    components.append({
        "component": "queue",
        "state": "configured" if use_celery else "inline",
        "detail": "Celery + Redis" if use_celery else "FastAPI BackgroundTasks (development)",
        "response_ms": None,
    })

    # --- API process ---
    components.append({
        "component": "api",
        "state": "operational",
        "detail": f"environment={os.getenv('APP_ENV', 'development')}",
        "response_ms": None,
    })

    conn = get_app_connection()
    try:
        c = conn.cursor()
        recent_failures = _scalar(
            c, "SELECT COUNT(*) FROM loan_cases WHERE status IN ('FAILED', 'REJECTED')"
        )
    finally:
        conn.close()

    return {
        "status": "success",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        "recent_pipeline_failures": recent_failures,
        "unmeasured": [
            not_measured("api_error_rate", "Request-level outcome counters, not currently aggregated."),
            not_measured("api_response_time", "Request duration histogram, not currently recorded."),
            not_measured("queue_depth", "Broker introspection; only available when Celery is enabled."),
        ],
    }


# =============================================================================
# AI operations and usage
# =============================================================================

def _provenance_rollup() -> List[Dict[str, Any]]:
    """Which providers and models actually produced appraisals.

    This is real provenance (P0-2) read back from appraisal_records. It is a
    count of appraisals per model, NOT a count of model calls - one appraisal
    involves several calls, and the platform does not record them individually.
    """
    conn = get_app_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT COALESCE(model_provider, 'not recorded'),
                      COALESCE(model_name, 'not recorded'),
                      COUNT(*),
                      SUM(CASE WHEN decision_allowed = 0 THEN 1 ELSE 0 END)
               FROM appraisal_records GROUP BY 1, 2 ORDER BY 3 DESC"""
        )
        return [
            {
                "provider": r[0],
                "model": r[1],
                "appraisals": int(r[2]),
                "gated_appraisals": int(r[3] or 0),
            }
            for r in c.fetchall()
        ]
    finally:
        conn.close()


@router.get("/ai-operations", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def ai_operations():
    """Model operations.

    Everything measurable here comes from the P0-2 provenance ledger, which
    records the model that produced each appraisal. Per-call telemetry - latency,
    retries, failovers, 429s, token counts - is not captured anywhere, so it is
    reported as not measured rather than estimated.
    """
    # Resolved through app.core.llm.active_provider() rather than read straight
    # from the environment. A configured SARVAM_API_KEY overrides the Groq path
    # entirely, so reporting GROQ_API_KEY's presence as "the provider" would tell
    # an operator the wrong thing about what is actually serving appraisals.
    from app.core.llm import active_provider

    return {
        "status": "success",
        "configured": active_provider(),
        "provenance": _provenance_rollup(),
        "unmeasured": [
            not_measured("requests", "Per-call LLM telemetry (llm_call_log). Not implemented."),
            not_measured("successful_requests", "Per-call LLM telemetry."),
            not_measured("failed_requests", "Per-call LLM telemetry."),
            not_measured("average_latency", "Per-call duration recording."),
            not_measured("retry_count", "The retry wrapper does not persist attempt counts."),
            not_measured("failover_events", "Model rollover is applied but not recorded."),
            not_measured("rate_limit_429_events", "Provider 429s are handled but not counted."),
            not_measured("token_usage", "Provider token accounting is not captured."),
        ],
    }


@router.get("/usage", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def platform_usage():
    """Processing volume per organization.

    Volume is real. Token usage and cost are not: without per-call accounting
    and provider pricing, any figure would be invented.
    """
    conn = get_app_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT institution_id, COUNT(*), MAX(updated_at)
               FROM loan_cases GROUP BY institution_id ORDER BY 2 DESC"""
        )
        volume = [
            {"organization_id": r[0], "cases_processed": int(r[1]), "last_activity": r[2]}
            for r in c.fetchall()
        ]
        appraisals = _scalar(c, "SELECT COUNT(*) FROM appraisal_records")
    finally:
        conn.close()

    return {
        "status": "success",
        "processing_volume": volume,
        "total_appraisals": appraisals,
        "by_model": _provenance_rollup(),
        "unmeasured": [
            not_measured("ai_requests", "Per-call LLM telemetry."),
            not_measured("token_usage", "Provider token accounting."),
            not_measured(
                "estimated_cost",
                "Token counts and a provider price list. Neither is available, "
                "and a cost figure must never be estimated.",
            ),
        ],
    }


# =============================================================================
# Platform configuration
# =============================================================================

@router.get("/configuration", dependencies=[Depends(SUPER_ADMIN_ONLY), Depends(rate_limit("admin"))])
async def platform_configuration():
    """Operational configuration, reported as posture rather than values.

    Secrets are reported only as configured / not configured. No API key, JWT
    secret, database URL, provider credential or HMAC secret is read here, and
    nothing on this endpoint is editable: these values are environment-owned, and
    offering a control that cannot take effect would be a lie about the system.
    """
    def present(var: str) -> str:
        return "configured" if (os.getenv(var) or "").strip() else "not configured"

    return {
        "status": "success",
        "editable": False,
        "note": "All values are environment-configured and read-only through this API.",
        "sections": [
            {
                "section": "Environment",
                "settings": [
                    {"key": "APP_ENV", "value": os.getenv("APP_ENV", "development"), "sensitive": False},
                    {"key": "Identity store", "value": "Postgres" if uses_postgres() else "SQLite (development)", "sensitive": False},
                ],
            },
            {
                "section": "Rate limits",
                "settings": [
                    {"key": "RATE_LIMIT_AUTH", "value": os.getenv("RATE_LIMIT_AUTH", "10"), "unit": "per minute per IP", "sensitive": False},
                    {"key": "RATE_LIMIT_READ", "value": os.getenv("RATE_LIMIT_READ", "300"), "unit": "per minute per tenant", "sensitive": False},
                    {"key": "RATE_LIMIT_WRITE", "value": os.getenv("RATE_LIMIT_WRITE", "60"), "unit": "per minute per tenant", "sensitive": False},
                    {"key": "RATE_LIMIT_AI", "value": os.getenv("RATE_LIMIT_AI", "20"), "unit": "per hour per tenant", "sensitive": False},
                    {"key": "RATE_LIMIT_ADMIN", "value": os.getenv("RATE_LIMIT_ADMIN", "120"), "unit": "per minute", "sensitive": False},
                    {"key": "RATE_LIMIT_WORKERS", "value": os.getenv("RATE_LIMIT_WORKERS", "1"), "sensitive": False},
                ],
            },
            {
                "section": "Model configuration",
                "settings": [
                    {"key": "PRIMARY_LLM_MODEL", "value": os.getenv("PRIMARY_LLM_MODEL", "not set"), "sensitive": False},
                    {"key": "FALLBACK_LLM_MODEL_1", "value": os.getenv("FALLBACK_LLM_MODEL_1", "not set"), "sensitive": False},
                    {"key": "FALLBACK_LLM_MODEL_2", "value": os.getenv("FALLBACK_LLM_MODEL_2", "not set"), "sensitive": False},
                    {"key": "LLM_MAX_TOKENS", "value": os.getenv("LLM_MAX_TOKENS", "not set"), "sensitive": False},
                ],
            },
            {
                "section": "Processing",
                "settings": [
                    {"key": "USE_CELERY", "value": os.getenv("USE_CELERY", "false"), "sensitive": False},
                    {"key": "TEMP_FILE_CLEANUP_MAX_AGE_SECONDS", "value": os.getenv("TEMP_FILE_CLEANUP_MAX_AGE_SECONDS", "3600"), "unit": "seconds", "sensitive": False},
                    {"key": "SUPABASE_STORAGE_BUCKET", "value": os.getenv("SUPABASE_STORAGE_BUCKET", "not set"), "sensitive": False},
                ],
            },
            {
                # Posture only. The values themselves are never read.
                "section": "Secrets",
                "settings": [
                    {"key": "GROQ_API_KEY", "value": present("GROQ_API_KEY"), "sensitive": True},
                    {"key": "SUPABASE_KEY", "value": present("SUPABASE_KEY"), "sensitive": True},
                    {"key": "AUDIT_HMAC_SECRET", "value": present("AUDIT_HMAC_SECRET"), "sensitive": True},
                    {"key": "AUTH_DATABASE_URL", "value": present("AUTH_DATABASE_URL"), "sensitive": True},
                ],
            },
        ],
    }
