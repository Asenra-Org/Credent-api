"""P1-3 - tenant-aware rate limiting for expensive operations.

One organization must not be able to exhaust shared capacity - LLM token quota,
ingestion throughput, worker slots - and deny service to every other tenant.
A single PDF ingestion triggers roughly eight LLM calls, so a modest loop from
one tenant can drain a provider quota in minutes. That is what happened during
development and took the whole pipeline down.

Design notes:

* **No Redis.** Celery is optional here (USE_CELERY defaults to false) and Redis
  is not part of the deployed infrastructure, so introducing it purely for
  distributed counters would add an operational dependency for a problem this
  deployment does not yet have. Limits are enforced per process using a sliding
  window. The limitation is explicit: with N API workers the effective ceiling
  is N x limit. Set RATE_LIMIT_WORKERS to divide the configured limits
  accordingly, or move to a shared store when the deployment scales out.

* **Keyed on the authenticated tenant**, never on client-supplied input, so a
  caller cannot escape its bucket by changing a header.

* **Tiered**, because a login attempt, a history read and a CAM generation have
  very different costs and very different abuse profiles.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


@dataclass(frozen=True)
class Tier:
    """A named limit: ``limit`` requests per ``window`` seconds."""

    name: str
    limit: int
    window: int


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _workers() -> int:
    """Number of API processes sharing the load, used to divide limits."""
    return _int_env("RATE_LIMIT_WORKERS", 1)


def _scaled(limit: int) -> int:
    return max(1, limit // _workers())


# Tiers are deliberately conservative for the expensive tier: a CAM generation
# is roughly eight LLM calls, so 20/hour/tenant is already ~160 model calls.
def build_tiers() -> Dict[str, Tier]:
    return {
        # Brute-force protection. Keyed by client IP, not tenant - the caller is
        # unauthenticated by definition at this point.
        "auth": Tier("auth", _scaled(_int_env("RATE_LIMIT_AUTH", 10)), 60),
        # Ordinary authenticated reads.
        "read": Tier("read", _scaled(_int_env("RATE_LIMIT_READ", 300)), 60),
        # Writes that are cheap but mutate state.
        "write": Tier("write", _scaled(_int_env("RATE_LIMIT_WRITE", 60)), 60),
        # Document ingestion, appraisal creation, CAM generation - anything that
        # spends provider tokens.
        "expensive_ai": Tier("expensive_ai", _scaled(_int_env("RATE_LIMIT_AI", 20)), 3600),
        # Platform administration, held separately so tenant abuse cannot
        # throttle operators out of their own system.
        "admin": Tier("admin", _scaled(_int_env("RATE_LIMIT_ADMIN", 120)), 60),
    }


TIERS: Dict[str, Tier] = build_tiers()

# Roles exempt from tenant limits: platform operators are not a tenant workload.
EXEMPT_ROLES = frozenset({"SUPERADMIN"})


class SlidingWindowLimiter:
    """In-process sliding-window counter. Thread-safe."""

    def __init__(self) -> None:
        self._events: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, tier: Tier, key: str, now: Optional[float] = None) -> Tuple[bool, int, int]:
        """Record an attempt.

        Returns ``(allowed, remaining, retry_after_seconds)``. When the request
        is rejected it is NOT recorded, so a caller hammering a closed window
        cannot push its own reset further away.
        """
        now = time.monotonic() if now is None else now
        cutoff = now - tier.window
        bucket = (tier.name, key)

        with self._lock:
            events = self._events[bucket]
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= tier.limit:
                retry_after = max(1, int(events[0] + tier.window - now) + 1)
                return False, 0, retry_after

            events.append(now)
            return True, tier.limit - len(events), 0

    def reset(self, key: Optional[str] = None) -> None:
        """Clear counters. Used by tests and by administrative recovery."""
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                for bucket in [b for b in self._events if b[1] == key]:
                    del self._events[bucket]

    def snapshot(self, tier_name: str, key: str) -> int:
        with self._lock:
            return len(self._events.get((tier_name, key), ()))


limiter = SlidingWindowLimiter()


def resolve_key(tenant_id: Optional[str], user_id: Optional[str], client_ip: Optional[str]) -> str:
    """Bucket key: tenant first, then user, then IP for unauthenticated calls.

    Tenant-level keying is what actually isolates organizations from each other;
    falling back to user or IP only matters before authentication has happened.
    """
    if tenant_id:
        return f"tenant:{tenant_id}"
    if user_id:
        return f"user:{user_id}"
    return f"ip:{client_ip or 'unknown'}"


def is_exempt(role: Optional[str]) -> bool:
    return bool(role) and role.upper() in EXEMPT_ROLES


def check_limit(
    tier_name: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    role: Optional[str] = None,
) -> Tuple[bool, int, int, Tier]:
    """Core check, framework-independent so it is directly unit-testable."""
    tier = TIERS.get(tier_name) or TIERS["read"]
    if is_exempt(role):
        return True, tier.limit, 0, tier
    key = resolve_key(tenant_id, user_id, client_ip)
    allowed, remaining, retry_after = limiter.check(tier, key)
    return allowed, remaining, retry_after, tier
