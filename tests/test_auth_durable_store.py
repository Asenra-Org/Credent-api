"""Regression tests for the durable identity store.

Authentication previously read and wrote SQLite unconditionally. On a container
platform with an ephemeral filesystem every account vanished on restart, so the
deployed API returned 401 for credentials that worked locally.

These tests cover the selection logic and the SQL translation. They do not
require a live Postgres: the translation is pure, and the Postgres branch is
exercised with a fake driver.
"""

import os
import sqlite3
import sys
import types

import pytest

from app.database import auth_db
from app.database.auth_db import (
    AUTH_DATABASE_URL_VARS,
    auth_database_url,
    get_auth_connection,
    init_auth_schema,
    translate_sql,
    uses_postgres,
)


@pytest.fixture(autouse=True)
def clear_auth_url(monkeypatch):
    for var in AUTH_DATABASE_URL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    yield


# ---------------------------------------------------------------------------
# backend selection
# ---------------------------------------------------------------------------

def test_defaults_to_sqlite_for_development():
    assert uses_postgres() is False
    assert auth_database_url() is None
    conn = get_auth_connection()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


@pytest.mark.parametrize("var", AUTH_DATABASE_URL_VARS)
def test_any_supported_env_var_selects_postgres(monkeypatch, var):
    monkeypatch.setenv(var, "postgresql://user:pw@db.example.supabase.co:5432/postgres")
    assert uses_postgres() is True
    assert auth_database_url().startswith("postgresql://")


def test_non_postgres_database_url_is_ignored(monkeypatch):
    """A DATABASE_URL left over from another project must not abort startup.

    Reproduces the real deploy failure: Render carried
    DATABASE_URL="sqlite+aiosqlite:///./intelliassess.db" from an unrelated
    service. Passing that to psycopg2 raised
    ProgrammingError: invalid dsn: missing "=" after "sqlite+aiosqlite..."
    and the container exited during lifespan startup.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./intelliassess.db")
    assert uses_postgres() is False
    assert auth_database_url() is None
    conn = get_auth_connection()          # must not raise
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


@pytest.mark.parametrize("value", [
    "sqlite+aiosqlite:///./intelliassess.db",
    "sqlite:///./local.db",
    "mysql://user:pw@host/db",
    "redis://localhost:6379/0",
    "not-a-url-at-all",
])
def test_only_postgres_schemes_are_accepted(monkeypatch, value):
    monkeypatch.setenv("AUTH_DATABASE_URL", value)
    assert uses_postgres() is False, f"{value!r} must not be treated as a Postgres DSN"


@pytest.mark.parametrize("value", [
    "postgresql://u:p@db.abc.supabase.co:5432/postgres",
    "postgres://u:p@host:5432/db",
    "postgresql+psycopg2://u:p@host:5432/db",
    "POSTGRESQL://u:p@host:5432/db",
])
def test_valid_postgres_schemes_are_accepted(monkeypatch, value):
    monkeypatch.setenv("AUTH_DATABASE_URL", value)
    assert uses_postgres() is True


def test_valid_auth_url_wins_over_invalid_database_url(monkeypatch):
    """A correct AUTH_DATABASE_URL must not be defeated by a stale DATABASE_URL."""
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./intelliassess.db")
    assert auth_database_url() == "postgresql://u:p@host:5432/db"


def test_rejection_log_does_not_leak_credentials(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://admin:SuperSecret123@db.internal:3306/app")
    auth_database_url()
    out = capsys.readouterr().out
    assert "SuperSecret123" not in out
    assert "db.internal" not in out
    assert "admin" not in out
    assert "scheme=mysql" in out


def test_blank_url_is_ignored(monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", "   ")
    assert uses_postgres() is False


def test_postgres_branch_uses_psycopg(monkeypatch):
    """The Postgres path must not silently fall back to SQLite."""
    captured = {}

    class FakeCursor:
        def execute(self, sql, params=None):
            captured.setdefault("sql", []).append(sql)
        def fetchone(self):
            return (1,)
        def close(self):
            pass

    class FakeConn:
        autocommit = False
        def cursor(self): return FakeCursor()
        def commit(self): captured["committed"] = True
        def close(self): captured["closed"] = True

    fake = types.ModuleType("psycopg2")
    def _connect(url, **kw):
        captured["url"] = url
        return FakeConn()

    fake.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://u:p@h:5432/db")

    conn = get_auth_connection()
    assert captured["url"] == "postgresql://u:p@h:5432/db"
    assert not isinstance(conn, sqlite3.Connection)


def test_init_auth_schema_is_noop_on_sqlite():
    assert init_auth_schema() is False


def test_init_auth_schema_creates_tables_on_postgres(monkeypatch):
    statements = []

    class FakeCursor:
        def execute(self, sql, params=None): statements.append(sql)
        def fetchone(self): return (1,)
        def close(self): pass

    class FakeConn:
        autocommit = False
        def cursor(self): return FakeCursor()
        def commit(self): pass
        def close(self): pass

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda url, **kw: FakeConn()
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://u:p@h:5432/db")

    assert init_auth_schema() is True
    joined = "\n".join(statements).lower()
    for table in ("users", "sessions", "tenant_memberships", "organizations",
                  "invitations", "system_state"):
        assert f"create table if not exists {table}" in joined, f"{table} DDL missing"
    # Every statement must be non-destructive.
    for bad in ("drop table", "delete from", "truncate"):
        assert bad not in joined, f"destructive statement in schema init: {bad}"


# ---------------------------------------------------------------------------
# SQL translation
# ---------------------------------------------------------------------------

def test_placeholders_are_translated():
    assert translate_sql("SELECT id FROM users WHERE id = ?") == \
        "SELECT id FROM users WHERE id = %s"


def test_multiple_placeholders():
    out = translate_sql("INSERT INTO sessions (id, user_id) VALUES (?, ?)")
    assert out.count("%s") == 2
    assert "?" not in out


def test_collate_nocase_becomes_lower_comparison():
    """Case-insensitive email lookup must survive the dialect change.

    Losing this would let two accounts differing only by case coexist, or make a
    correct login fail because of capitalisation.
    """
    out = translate_sql("SELECT id FROM users WHERE email = ? COLLATE NOCASE")
    assert "COLLATE" not in out.upper()
    assert "LOWER(email) = LOWER(%s)" in out


def test_collate_nocase_in_real_login_query():
    sql = """
        SELECT id, password_hash, is_active, is_locked, lockout_until, mfa_enabled
        FROM users
        WHERE email = ? COLLATE NOCASE
    """
    out = translate_sql(sql)
    assert "LOWER(email) = LOWER(%s)" in out
    assert "?" not in out
    assert "NOCASE" not in out.upper()


def test_sqlite_datetime_default_is_translated():
    assert "NOW()" in translate_sql("created_at TEXT DEFAULT (datetime('now'))")


def test_insert_or_replace_is_translated():
    assert "INSERT OR REPLACE" not in translate_sql(
        "INSERT OR REPLACE INTO companies (id) VALUES (?)"
    )


def test_ordinary_sql_passes_through_unchanged():
    sql = "UPDATE users SET mfa_enabled = 1 WHERE id = %s"
    assert translate_sql(sql) == sql


# ---------------------------------------------------------------------------
# the identity layer no longer bypasses this module
# ---------------------------------------------------------------------------

def test_no_auth_module_opens_sqlite_directly():
    """A new direct SQLite call in auth would reintroduce the ephemeral-store bug."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for rel in ("security/auth_service.py", "security/dependencies.py",
                "routes/auth.py", "routes/admin.py"):
        text = (root / rel).read_text(encoding="utf-8", errors="ignore")
        if "get_sqlite_connection" in text:
            offenders.append(rel)
    assert not offenders, f"identity code bypassing the durable store: {offenders}"


def test_sqlite_still_refused_in_production(monkeypatch):
    """P0-5 must still hold: no SQLite identity store in production."""
    from app.core.db_policy import ProductionDatabaseError

    monkeypatch.setenv("APP_ENV", "production")
    for var in AUTH_DATABASE_URL_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ProductionDatabaseError):
        get_auth_connection()


def test_production_with_postgres_is_allowed(monkeypatch):
    """With a real identity store configured, production must start."""
    class FakeConn:
        autocommit = False
        def cursor(self): raise AssertionError("not needed")
        def close(self): pass

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda url, **kw: FakeConn()
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://u:p@h:5432/db")

    conn = get_auth_connection()   # must not raise
    assert conn is not None
