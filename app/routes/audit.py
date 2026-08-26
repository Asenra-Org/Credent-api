# =============================================================================
# CRESEM - Audit event explorer
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
"""Read access to the hash-linked audit chain.

The chain has been written correctly since ASE-60 and nothing could read it,
which made the Audit & Security console impossible to build. This module adds
the read path and nothing else: no write, no update, no delete. The SQLite
triggers that make ``audit_logs`` append-only are untouched.

Authorization is deliberately narrow. Audit events name actors and carry
before/after state, so they are visible to ORG_ADMIN (their own organization)
and SUPER_ADMIN (any organization, named explicitly). Analysts and managers see
audit history scoped to a single case through ``GET /cases/{id}/audit`` instead.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database.database import get_sqlite_connection, list_audit_events
from app.security.audit_service import verify_tenant_chain
from app.security.dependencies import get_current_tenant, get_current_user_and_session, require_role
from app.security.rate_limit_dependency import rate_limit

router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])

AUDIT_READERS = ["ORG_ADMIN", "SUPER_ADMIN"]


def _resolve_scope(role: str, tenant_id: str, organization_id: Optional[str]) -> str:
    """Decide which tenant's chain the caller may read.

    A SUPER_ADMIN operates the platform and may name any organization. Every
    other role is pinned to the tenant their token was issued for, and passing
    ``organization_id`` for a different tenant is refused rather than silently
    ignored - a caller who believes they are reading another organization's
    audit log should be told they cannot, not handed their own.
    """
    if role == "SUPER_ADMIN":
        return organization_id or tenant_id
    if organization_id and organization_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Not permitted to read audit events for another organization",
        )
    return tenant_id


@router.get(
    "/events",
    dependencies=[Depends(rate_limit("read"))],
)
async def get_audit_events(
    organization_id: Optional[str] = Query(
        default=None, description="SUPER_ADMIN only. Defaults to the caller's own organization."
    ),
    case_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO-8601 lower bound, inclusive"),
    date_to: Optional[str] = Query(default=None, description="ISO-8601 upper bound, inclusive"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    role: str = Depends(require_role(AUDIT_READERS)),
    tenant_id: str = Depends(get_current_tenant),
):
    """Filtered audit events for one organization."""
    scope = _resolve_scope(role, tenant_id, organization_id)

    result = list_audit_events(
        tenant_id=scope,
        case_id=case_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {"status": "success", "organization_id": scope, **result}


@router.get(
    "/verify",
    dependencies=[Depends(rate_limit("read"))],
)
async def verify_audit_chain(
    organization_id: Optional[str] = Query(default=None),
    role: str = Depends(require_role(AUDIT_READERS)),
    tenant_id: str = Depends(get_current_tenant),
    user_ctx: dict = Depends(get_current_user_and_session),
):
    """Recompute the HMAC chain for one organization and report its integrity.

    This is the existing ``verify_tenant_chain`` routine exposed for operators.
    It reads and recomputes; it never repairs. A tampered chain must surface as
    invalid, not be quietly fixed.
    """
    scope = _resolve_scope(role, tenant_id, organization_id)

    conn = get_sqlite_connection()
    try:
        result = verify_tenant_chain(conn, scope)
    finally:
        conn.close()

    return {"status": "success", "organization_id": scope, "chain": result}
