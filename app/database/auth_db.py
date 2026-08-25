"""Durable storage for the identity layer.

Authentication - users, sessions, MFA secrets, lockout counters, tenant
memberships - was read and written exclusively through SQLite. On a container
platform with an ephemeral filesystem (Render, Fly, most PaaS) that file is
recreated empty on every deploy and restart, so every account silently
disappeared and login returned 401 on the deployed instance while working
perfectly on a developer machine.

Supabase is Postgres, so the fix does not require rewriting the twenty-odd SQL
statements in ``app.security.auth_service``. This module returns a connection to
whichever backend is configured and normalises the two dialect differences that
actually appear in those statements:

  * placeholders - sqlite3 uses ``?``, psycopg2 uses ``%s``
  * case-insensitive email matching - SQLite spells it ``COLLATE NOCASE``,
    Postgres needs ``LOWER(col) = LOWER(val)``

Development and tests keep using SQLite unchanged, so behaviour there is
identical and the existing suite exercises the same code path it always did.
Production uses Postgres when ``AUTH_DATABASE_URL`` is set.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import Any, Optional

from app.core.db_policy import assert_sqlite_permitted

# Supabase exposes a standard Postgres connection string under
# Project Settings -> Database -> Connection string -> URI.
AUTH_DATABASE_URL_VARS = ("AUTH_DATABASE_URL", "DATABASE_URL", "SUPABASE_DB_URL")

_local = threading.local()


# Only a genuine Postgres DSN can back the identity store. DATABASE_URL is a
# conventional name that other tooling also claims, so a value left over from a
# different project (for example a SQLAlchemy "sqlite+aiosqlite:///..." URL) can
# easily be present. Handing that to psycopg2 aborts application startup with
#   psycopg2.ProgrammingError: invalid dsn: missing "=" after "sqlite+aiosqlite..."
# so the scheme is validated here and anything else is ignored rather than
# crashing the process.
_POSTGRES_SCHEMES = ("postgresql://", "postgres://", "postgresql+psycopg2://")


def is_postgres_dsn(value: str) -> bool:
    return (value or "").strip().lower().startswith(_POSTGRES_SCHEMES)


def _scheme_of(value: str) -> str:
    """Scheme prefix only - never the host, user or password."""
    head = (value or "").split("://", 1)[0]
    return head[:32] if head else "(none)"


def auth_database_url() -> Optional[str]:
    """Postgres DSN for the identity store, if one is validly configured.

    Returns None when nothing is set, or when the configured value is not a
    Postgres DSN - in which case SQLite remains in use and (in production) the
    P0-5 guard still refuses to start with an ephemeral identity store.
    """
    for var in AUTH_DATABASE_URL_VARS:
        value = (os.getenv(var) or "").strip()
        if not value:
            continue
        if is_postgres_dsn(value):
            return value
        # Log the variable and scheme only. The DSN carries credentials.
        print(
            f"[AUTH-DB] Ignoring {var}: not a Postgres DSN "
            f"(scheme={_scheme_of(value)}). The identity store needs a "
            f"postgresql:// URL; set AUTH_DATABASE_URL."
        )
    return None


def uses_postgres() -> bool:
    return auth_database_url() is not None


# ---------------------------------------------------------------------------
# SQL translation
# ---------------------------------------------------------------------------

_RE_COLLATE_NOCASE = re.compile(
    r"(\b[\w.]+\b)\s*=\s*\?\s+COLLATE\s+NOCASE", re.IGNORECASE
)


def translate_sql(sql: str) -> str:
    """Rewrite SQLite-flavoured SQL for Postgres.

    Only the constructs that actually occur in the identity queries are handled;
    anything else is passed through untouched so an unsupported statement fails
    loudly rather than being silently mistranslated.
    """
    # "email = ? COLLATE NOCASE" -> "LOWER(email) = LOWER(?)"
    sql = _RE_COLLATE_NOCASE.sub(r"LOWER(\1) = LOWER(?)", sql)
    sql = re.sub(r"\bCOLLATE\s+NOCASE\b", "", sql, flags=re.IGNORECASE)

    # Placeholders. Done last so the rewrites above can rely on '?'.
    sql = sql.replace("?", "%s")

    # SQLite-only DDL/idioms that would otherwise reach Postgres.
    sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
    sql = re.sub(r"datetime\(\s*'now'\s*\)", "NOW()", sql, flags=re.IGNORECASE)
    return sql


class _PgCursor:
    """Cursor wrapper that translates SQL before execution."""

    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, sql: str, params: Any = None):
        return self._cursor.execute(translate_sql(sql), params or ())

    def executemany(self, sql: str, seq: Any):
        return self._cursor.executemany(translate_sql(sql), seq)

    def __getattr__(self, item):
        return getattr(self._cursor, item)


class _PgConnection:
    """Connection wrapper handing out translating cursors."""

    def __init__(self, conn: Any):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _PgCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._conn.__exit__(*exc) if hasattr(self._conn, "__exit__") else None
        return False


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_auth_connection(timeout: float = 30.0):
    """Return a connection to the identity store.

    Postgres when ``AUTH_DATABASE_URL`` is configured, otherwise SQLite - which
    remains the correct choice for development and tests and is still refused in
    production by the P0-5 policy guard.
    """
    url = auth_database_url()
    if url:
        import psycopg2

        conn = psycopg2.connect(url, connect_timeout=int(timeout))
        conn.autocommit = False
        return _PgConnection(conn)

    # Imported here to avoid a circular import at module load.
    from app.database.database import DB_PATH

    assert_sqlite_permitted("get_auth_connection")
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = None
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Postgres DDL mirroring the SQLite identity tables. Types are chosen so the
# existing Python code needs no changes: integer flags stay integers rather than
# becoming booleans, and ids stay text.
POSTGRES_AUTH_DDL = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        is_locked INTEGER DEFAULT 0,
        failed_login_count INTEGER DEFAULT 0,
        lockout_until TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        auth_provider TEXT DEFAULT 'local',
        mfa_secret TEXT,
        mfa_enabled INTEGER DEFAULT 0,
        last_login_at TIMESTAMP
    )
    """,
    # Existing Postgres deployments predate last_login_at; add it separately so
    # the CREATE TABLE above stays a no-op on an already-provisioned database.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
    """
    CREATE TABLE IF NOT EXISTS organizations (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_memberships (
        user_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        role TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, tenant_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        refresh_token_hash TEXT NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        revoked_at TIMESTAMP,
        is_revoked INTEGER DEFAULT 0,
        ip_address TEXT,
        user_agent TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS invitations (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        role TEXT NOT NULL,
        token_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        is_bootstrapped INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant ON tenant_memberships(tenant_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (LOWER(email))",
)


def init_auth_schema() -> bool:
    """Create the identity tables in Postgres. No-op when using SQLite.

    Safe to run repeatedly: every statement is IF NOT EXISTS, and no existing
    row is altered or removed.
    """
    if not uses_postgres():
        return False

    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        for statement in POSTGRES_AUTH_DDL:
            cursor.execute(statement)
        cursor.execute("SELECT 1 FROM system_state WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO system_state (id, is_bootstrapped) VALUES (1, 0)")
        conn.commit()
        return True
    finally:
        conn.close()
