# =============================================================================
# CRESEM - Case listing and case workspace routes
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
"""Read endpoints for the case queue and the case workspace.

Before this module the only way to reach a case was ``GET
/documents/ingest/status/{case_id}`` - one row at a time, by id. There was no
way to list cases, so no queue, no case table and no pipeline view could exist
for any role.

Three rules govern everything here:

* **Tenant scoping is enforced in SQL, not in the response builder.** Every
  query filters on the ``institution_id`` the access token was issued for. A
  caller cannot widen that by passing a parameter; there is no parameter that
  would let them.
* **Nothing is invented.** Fields that were never recorded are returned as
  null. A case with no borrower name reads as null, not as a placeholder.
* **The P0-4 gate is surfaced, never smoothed over.** ``decision_allowed``,
  ``analysis_status`` and ``missing_required`` travel with every case so a
  client can distinguish an incomplete analysis from an underwriting
  conclusion.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.case_status import all_statuses, is_valid_status
from app.database.database import (
    get_case_appraisal,
    get_case_detail,
    list_case_documents,
    list_cases,
)
from app.database.database import list_audit_events
from app.security.dependencies import get_current_tenant, require_role
from app.security.rate_limit_dependency import rate_limit

router = APIRouter(prefix="/api/v1/cases", tags=["Cases"])

# Everyone who can see a case at all. VIEWER is read-only by virtue of there
# being no write endpoint in this module.
CASE_READERS = ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN", "VIEWER"]

# The case audit trail names the people who acted on a case. VIEWER is excluded
# because a read-only observer has no need for the actor-level record.
CASE_AUDIT_READERS = ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "ORG_ADMIN"]


@router.get(
    "",
    dependencies=[Depends(require_role(CASE_READERS)), Depends(rate_limit("read"))],
)
async def get_cases(
    status: Optional[List[str]] = Query(
        default=None,
        description="Filter by lifecycle status. Repeat the parameter to pass several.",
    ),
    assigned_to: Optional[str] = Query(default=None),
    created_by: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=200),
    sort: str = Query(default="created_at"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(get_current_tenant),
):
    """List cases for the authenticated tenant."""
    if status:
        invalid = [s for s in status if not is_valid_status(s)]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Unknown status filter",
                    "invalid": invalid,
                    "allowed": all_statuses(),
                },
            )

    result = list_cases(
        tenant_id=tenant_id,
        status=status,
        assigned_to=assigned_to,
        created_by=created_by,
        search=search,
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
    )
    return {"status": "success", **result}


@router.get(
    "/statuses",
    dependencies=[Depends(require_role(CASE_READERS)), Depends(rate_limit("read"))],
)
async def get_statuses():
    """The closed set of lifecycle states, so clients never invent their own."""
    return {"status": "success", "statuses": all_statuses()}


@router.get(
    "/{case_id}",
    dependencies=[Depends(require_role(CASE_READERS)), Depends(rate_limit("read"))],
)
async def get_case_workspace(case_id: str, tenant_id: str = Depends(get_current_tenant)):
    """One case with its linked appraisal and documents.

    A case belonging to another tenant is reported as 404 rather than 403: a
    404 does not confirm that the id exists somewhere else on the platform.
    """
    case = get_case_detail(case_id, tenant_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    appraisal = get_case_appraisal(case_id, tenant_id)
    documents = list_case_documents(case_id, tenant_id)

    return {
        "status": "success",
        "case": case,
        # None when no appraisal has been linked yet. Callers must render that
        # as "no appraisal yet", never as an empty appraisal.
        "appraisal": appraisal,
        "documents": documents,
    }


@router.get(
    "/{case_id}/documents",
    dependencies=[Depends(require_role(CASE_READERS)), Depends(rate_limit("read"))],
)
async def get_case_documents(case_id: str, tenant_id: str = Depends(get_current_tenant)):
    """Documents attached to a case."""
    case = get_case_detail(case_id, tenant_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return {"status": "success", "items": list_case_documents(case_id, tenant_id)}


@router.get(
    "/{case_id}/audit",
    dependencies=[Depends(require_role(CASE_AUDIT_READERS)), Depends(rate_limit("read"))],
)
async def get_case_audit_trail(
    case_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(get_current_tenant),
):
    """The audit trail for one case.

    The case is resolved tenant-scoped first, so a caller cannot read another
    tenant's audit events by guessing a case id.
    """
    case = get_case_detail(case_id, tenant_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    result = list_audit_events(
        tenant_id=tenant_id, case_id=case_id, limit=limit, offset=offset
    )
    return {"status": "success", **result}
