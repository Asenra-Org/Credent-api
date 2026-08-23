"""P1-3 - tenant-aware rate limiting.

The property that matters: one organization cannot exhaust shared LLM quota or
ingestion capacity and deny service to another. These tests prove isolation,
enforcement, and that ordinary traffic below the limit is untouched.
"""

import time

import pytest

from app.core.rate_limit import (
    TIERS,
    SlidingWindowLimiter,
    Tier,
    check_limit,
    is_exempt,
    limiter,
    resolve_key,
)


@pytest.fixture(autouse=True)
def clean_limiter():
    limiter.reset()
    yield
    limiter.reset()


# ---------------------------------------------------------------------------
# keying and isolation
# ---------------------------------------------------------------------------

def test_key_prefers_tenant_over_user_and_ip():
    assert resolve_key("T1", "U1", "1.2.3.4") == "tenant:T1"
    assert resolve_key(None, "U1", "1.2.3.4") == "user:U1"
    assert resolve_key(None, None, "1.2.3.4") == "ip:1.2.3.4"
    assert resolve_key(None, None, None) == "ip:unknown"


def test_tenant_a_cannot_exhaust_tenant_b_allowance():
    """The core P1-3 requirement."""
    tier = TIERS["expensive_ai"]

    for _ in range(tier.limit):
        allowed, *_ = check_limit("expensive_ai", tenant_id="TENANT-A")
        assert allowed

    # Tenant A is now blocked.
    allowed_a, remaining_a, retry_after, _ = check_limit("expensive_ai", tenant_id="TENANT-A")
    assert allowed_a is False
    assert remaining_a == 0
    assert retry_after > 0

    # Tenant B is completely unaffected.
    allowed_b, remaining_b, _, _ = check_limit("expensive_ai", tenant_id="TENANT-B")
    assert allowed_b is True
    assert remaining_b == tier.limit - 1


def test_same_tenant_different_users_share_the_tenant_budget():
    """Quota belongs to the organization, not to individual analysts."""
    tier = TIERS["expensive_ai"]
    for i in range(tier.limit):
        allowed, *_ = check_limit("expensive_ai", tenant_id="T1", user_id=f"user-{i}")
        assert allowed
    allowed, *_ = check_limit("expensive_ai", tenant_id="T1", user_id="another-user")
    assert allowed is False, "a new user must not reset the tenant's budget"


def test_tiers_are_independent():
    tier = TIERS["expensive_ai"]
    for _ in range(tier.limit):
        check_limit("expensive_ai", tenant_id="T1")
    assert check_limit("expensive_ai", tenant_id="T1")[0] is False
    # Cheap reads still work while the AI budget is spent.
    assert check_limit("read", tenant_id="T1")[0] is True


# ---------------------------------------------------------------------------
# enforcement
# ---------------------------------------------------------------------------

def test_requests_below_the_limit_are_allowed():
    tier = TIERS["read"]
    for i in range(min(50, tier.limit)):
        allowed, remaining, retry_after, _ = check_limit("read", tenant_id="T-NORMAL")
        assert allowed, f"normal request {i} should not be limited"
        assert retry_after == 0


def test_rejected_request_is_not_counted():
    """Hammering a closed window must not push the reset further away."""
    lim = SlidingWindowLimiter()
    tier = Tier("t", 2, 60)
    assert lim.check(tier, "k")[0] is True
    assert lim.check(tier, "k")[0] is True
    assert lim.check(tier, "k")[0] is False
    assert lim.check(tier, "k")[0] is False
    assert lim.snapshot("t", "k") == 2, "rejected attempts must not be recorded"


def test_window_slides_and_allows_again():
    lim = SlidingWindowLimiter()
    tier = Tier("t", 2, 10)
    now = 1000.0
    assert lim.check(tier, "k", now=now)[0] is True
    assert lim.check(tier, "k", now=now)[0] is True
    assert lim.check(tier, "k", now=now)[0] is False
    # Past the window, the budget is restored.
    assert lim.check(tier, "k", now=now + 11)[0] is True


def test_retry_after_is_positive_and_bounded_by_window():
    lim = SlidingWindowLimiter()
    tier = Tier("t", 1, 30)
    lim.check(tier, "k", now=500.0)
    allowed, _, retry_after = lim.check(tier, "k", now=500.0)
    assert allowed is False
    assert 0 < retry_after <= tier.window + 1


def test_limiter_is_threadsafe_under_concurrency():
    import threading

    lim = SlidingWindowLimiter()
    tier = Tier("t", 100, 60)
    granted = []
    lock = threading.Lock()

    def worker():
        for _ in range(50):
            ok, *_ = lim.check(tier, "shared")
            if ok:
                with lock:
                    granted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(granted) == tier.limit, "concurrent access must not over-grant"


# ---------------------------------------------------------------------------
# privileged operations
# ---------------------------------------------------------------------------

def test_superadmin_is_exempt():
    assert is_exempt("SUPERADMIN") is True
    assert is_exempt("superadmin") is True
    assert is_exempt("ORG_ADMIN") is False
    assert is_exempt(None) is False


def test_platform_operator_not_throttled_by_tenant_abuse():
    tier = TIERS["expensive_ai"]
    for _ in range(tier.limit + 5):
        check_limit("expensive_ai", tenant_id="NOISY-TENANT")
    assert check_limit("expensive_ai", tenant_id="NOISY-TENANT")[0] is False
    allowed, *_ = check_limit("expensive_ai", tenant_id="NOISY-TENANT", role="SUPERADMIN")
    assert allowed is True


def test_auth_tier_is_keyed_by_ip_when_unauthenticated():
    tier = TIERS["auth"]
    for _ in range(tier.limit):
        assert check_limit("auth", client_ip="10.0.0.1")[0] is True
    assert check_limit("auth", client_ip="10.0.0.1")[0] is False
    # A different source address is a different bucket.
    assert check_limit("auth", client_ip="10.0.0.2")[0] is True


def test_unknown_tier_falls_back_to_read_not_unlimited():
    allowed, _, _, tier = check_limit("does-not-exist", tenant_id="T1")
    assert allowed is True
    assert tier.name == "read", "unknown tiers must not bypass limiting"


# ---------------------------------------------------------------------------
# no information leakage
# ---------------------------------------------------------------------------

def test_rejection_metadata_leaks_no_tenant_information():
    from app.security.rate_limit_dependency import rate_limit
    from fastapi import HTTPException
    import asyncio

    class FakeClient:
        host = "10.0.0.9"

    class FakeRequest:
        client = FakeClient()
        headers = {}
        state = None

    tier = TIERS["auth"]
    dep = rate_limit("auth")
    for _ in range(tier.limit):
        asyncio.run(dep(FakeRequest()))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(FakeRequest()))

    detail = str(exc.value.detail)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    for leak in ("tenant", "TENANT", "10.0.0.9", "user:"):
        assert leak not in detail, f"rejection detail leaked {leak!r}"


def test_dependency_rejects_unknown_tier_at_construction():
    from app.security.rate_limit_dependency import rate_limit

    with pytest.raises(ValueError):
        rate_limit("not-a-real-tier")
