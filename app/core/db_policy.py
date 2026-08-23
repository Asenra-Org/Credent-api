"""P0-5 - environment-aware database policy.

Previously, missing Supabase credentials produced a warning and the application
carried on against a local SQLite file. In a container that file lives on
ephemeral storage, so a misconfigured production deploy silently wrote lending
records to disk that vanishes on restart - with no backup, no replication and
single-writer concurrency across tenants.

SQLite remains fully supported for development and tests. In production it is
refused: if the configured production database is unavailable, the application
fails fast rather than pretending to be healthy.
"""

from __future__ import annotations

import os
from typing import Optional

DEVELOPMENT = "development"
TEST = "test"
PRODUCTION = "production"

_PRODUCTION_ALIASES = {"production", "prod", "live"}
_TEST_ALIASES = {"test", "testing", "ci"}


class ProductionDatabaseError(RuntimeError):
    """Raised when production is configured without a usable production database."""


def current_environment() -> str:
    """Resolve the deployment environment.

    Defaults to development so a developer who has set nothing keeps the SQLite
    workflow. Production must be opted into explicitly via APP_ENV.
    """
    raw = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or DEVELOPMENT).strip().lower()
    if raw in _PRODUCTION_ALIASES:
        return PRODUCTION
    if raw in _TEST_ALIASES:
        return TEST
    return DEVELOPMENT


def is_production() -> bool:
    return current_environment() == PRODUCTION


def sqlite_allowed() -> bool:
    """SQLite is permitted outside production only."""
    return not is_production()


def has_production_database(
    supabase_url: Optional[str] = None,
    supabase_key: Optional[str] = None,
) -> bool:
    url = supabase_url if supabase_url is not None else os.getenv("SUPABASE_URL")
    key = supabase_key if supabase_key is not None else os.getenv("SUPABASE_KEY")
    return bool(url and key)


def enforce_database_policy(
    supabase_url: Optional[str] = None,
    supabase_key: Optional[str] = None,
) -> str:
    """Validate the database configuration for the current environment.

    Returns the backend that will be used ("supabase" or "sqlite").
    Raises ProductionDatabaseError in production when no production database is
    configured - the application must not start as a lending system without its
    durable store.
    """
    env = current_environment()
    configured = has_production_database(supabase_url, supabase_key)

    if env == PRODUCTION:
        if not configured:
            raise ProductionDatabaseError(
                "APP_ENV=production requires SUPABASE_URL and SUPABASE_KEY. "
                "Refusing to start: SQLite fallback is disabled in production "
                "because lending records would be written to ephemeral storage."
            )
        return "supabase"

    return "supabase" if configured else "sqlite"


def assert_sqlite_permitted(context: str = "database access") -> None:
    """Guard the SQLite path itself, so no code route can slip past the policy."""
    if not sqlite_allowed():
        raise ProductionDatabaseError(
            f"SQLite is not permitted in production ({context}). "
            "Configure SUPABASE_URL and SUPABASE_KEY."
        )
