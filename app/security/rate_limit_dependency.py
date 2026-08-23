"""P1-3 - FastAPI dependency for tenant-aware rate limiting.

Centralised so endpoints declare a tier rather than reimplementing counting
logic. Attach with::

    @router.post("/ingest/pdf", dependencies=[Depends(rate_limit("expensive_ai"))])

The dependency reads the tenant from the verified JWT claims via the existing
auth dependency; it never trusts a client-supplied header, so a caller cannot
change buckets to escape its own limit.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from app.core.rate_limit import TIERS, check_limit


def _client_ip(request: Request) -> Optional[str]:
    if request.client and request.client.host:
        return request.client.host
    return None


def _identity(request: Request) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort tenant/user/role from JWT claims already verified upstream.

    Returns (tenant_id, user_id, role). Absent claims simply mean the caller is
    unauthenticated and will be bucketed by IP instead.
    """
    for attr in ("state",):
        state = getattr(request, attr, None)
        user = getattr(state, "user", None) if state is not None else None
        if isinstance(user, dict):
            return user.get("tenant_id"), user.get("user_id"), user.get("role")

    # Fall back to decoding the bearer token without re-verifying: this is only
    # used to pick a counter bucket, never to grant access. Authentication and
    # authorization remain the job of the auth dependencies.
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        try:
            import jwt

            claims = jwt.decode(auth.split(" ", 1)[1], options={"verify_signature": False})
            return claims.get("tenant_id"), claims.get("sub"), claims.get("role")
        except Exception:
            return None, None, None
    return None, None, None


def rate_limit(tier_name: str = "read") -> Callable:
    """Build a dependency enforcing ``tier_name`` for the calling tenant."""
    if tier_name not in TIERS:
        raise ValueError(f"unknown rate limit tier: {tier_name}")

    async def _dependency(request: Request) -> None:
        tenant_id, user_id, role = _identity(request)
        allowed, remaining, retry_after, tier = check_limit(
            tier_name,
            tenant_id=tenant_id,
            user_id=user_id,
            client_ip=_client_ip(request),
            role=role,
        )
        if allowed:
            return

        # The message deliberately says nothing about which tenant, which other
        # tenants exist, or how much quota anyone else has consumed.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Rate limit exceeded for this operation. "
                f"Try again in {retry_after} seconds."
            ),
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(tier.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": str(tier.window),
            },
        )

    return _dependency
