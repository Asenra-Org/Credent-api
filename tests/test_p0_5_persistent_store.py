"""P0-5 - production must not run on ephemeral SQLite, and must still start.

Two requirements were in conflict:

  * P0-5: production must never write lending records to SQLite, because in a
    container that file is ephemeral - no backup, no replication, gone on
    restart.
  * Commit 687b3f8: production was crashing at startup because ``init_db()``
    opens a connection at module import and the case tables had no Postgres
    implementation.

687b3f8 resolved it by emptying ``assert_sqlite_permitted()`` to ``pass``,
which removed the control rather than satisfying it. These tests pin the
correct resolution: the guard is active, AND production startup does not
require SQLite, because the application schema now has a Postgres path.
"""

import importlib

import pytest

from app.core import db_policy
from app.core.db_policy import ProductionDatabaseError


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------

class TestSqliteIsRefusedInProduction:
    def test_direct_call_is_refused(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(ProductionDatabaseError):
            db_policy.assert_sqlite_permitted("unit-test")

    def test_the_guard_is_not_a_no_op(self):
        """687b3f8 replaced the body with `pass`. It must never be empty again."""
        import inspect

        source = inspect.getsource(db_policy.assert_sqlite_permitted)
        assert "raise ProductionDatabaseError" in source

    @pytest.mark.parametrize("alias", ["production", "prod", "live"])
    def test_every_production_alias_refuses(self, monkeypatch, alias):
        monkeypatch.setenv("APP_ENV", alias)
        with pytest.raises(ProductionDatabaseError):
            db_policy.assert_sqlite_permitted("unit-test")

    def test_sqlite_connection_factory_is_refused_in_production(self, monkeypatch):
        from app.database.database import get_sqlite_connection

        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(ProductionDatabaseError):
            get_sqlite_connection()


# ---------------------------------------------------------------------------
# SQLite remains available outside production
# ---------------------------------------------------------------------------

class TestSqliteStillWorksForDevelopmentAndTest:
    @pytest.mark.parametrize("env", ["development", "dev-unset", "test", "testing", "ci"])
    def test_permitted_outside_production(self, monkeypatch, env):
        if env == "dev-unset":
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", env)
        db_policy.assert_sqlite_permitted("unit-test")   # must not raise

    def test_development_can_open_a_connection(self, monkeypatch):
        from app.database.database import get_sqlite_connection

        monkeypatch.setenv("APP_ENV", "development")
        conn = get_sqlite_connection()
        try:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        finally:
            conn.close()

    def test_test_environment_can_open_a_connection(self, monkeypatch):
        from app.database.database import get_sqlite_connection

        monkeypatch.setenv("APP_ENV", "test")
        conn = get_sqlite_connection()
        try:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The persistent path
# ---------------------------------------------------------------------------

class TestProductionUsesThePersistentStore:
    def test_app_store_selects_postgres_when_a_dsn_is_configured(self, monkeypatch):
        from app.database import database

        monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://user:pw@db.example.com:5432/postgres")
        assert database.uses_postgres_app() is True
        assert database.app_database_url().startswith("postgresql://")

    def test_app_store_falls_back_to_sqlite_without_a_dsn(self, monkeypatch):
        from app.database import database

        for var in ("AUTH_DATABASE_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
            monkeypatch.delenv(var, raising=False)
        assert database.uses_postgres_app() is False

    def test_a_non_postgres_dsn_is_ignored(self, monkeypatch):
        """A stale sqlite:// URL from another project must not be handed to psycopg2."""
        from app.database import database

        for var in ("AUTH_DATABASE_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./other.db")
        assert database.uses_postgres_app() is False

    def test_postgres_schema_covers_every_application_table(self):
        from app.database.database import POSTGRES_APP_DDL

        ddl = " ".join(POSTGRES_APP_DDL).lower()
        for table in (
            "companies", "appraisal_records", "loan_cases", "case_documents",
            "case_reviews", "institution_policies", "audit_logs", "audit_chain_heads",
        ):
            assert f"create table if not exists {table}" in ddl, f"{table} missing from Postgres DDL"

    def test_postgres_schema_keeps_audit_logs_append_only(self):
        """The SQLite triggers have a Postgres equivalent, or the chain is only
        tamper-evident in development."""
        from app.database.database import POSTGRES_AUDIT_APPEND_ONLY

        sql = " ".join(POSTGRES_AUDIT_APPEND_ONLY).lower()
        assert "before update on audit_logs" in sql
        assert "before delete on audit_logs" in sql
        assert "append-only" in sql

    def test_postgres_schema_carries_the_p0_2_provenance_columns(self):
        from app.database.database import POSTGRES_APP_DDL

        ddl = " ".join(POSTGRES_APP_DDL).lower()
        for column in (
            "model_provider", "model_name", "prompt_version", "agent_version",
            "analysis_status", "decision_allowed", "degraded_components",
        ):
            assert column in ddl, f"{column} missing from the Postgres appraisal schema"


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

class TestProductionStartupDoesNotRequireSqlite:
    def test_init_db_takes_the_postgres_branch_without_touching_sqlite(self, monkeypatch):
        """The actual 687b3f8 crash: init_db() opened SQLite at import time.

        With Postgres configured it must not call the SQLite factory at all, so
        importing the module in production no longer raises.
        """
        from app.database import database

        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setattr(database, "uses_postgres_app", lambda: True)

        called = {"sqlite": False, "postgres": False}

        def _sqlite_must_not_be_called(*a, **k):
            called["sqlite"] = True
            raise AssertionError("init_db opened SQLite in production")

        monkeypatch.setattr(database, "get_sqlite_connection", _sqlite_must_not_be_called)
        monkeypatch.setattr(database, "init_app_schema", lambda: called.__setitem__("postgres", True))
        monkeypatch.setattr(database, "_get_supabase", lambda: object())

        database.init_db()

        assert called["postgres"] is True
        assert called["sqlite"] is False

    def test_production_without_a_persistent_store_fails_closed(self, monkeypatch):
        """No Postgres and no Supabase in production: refuse to start."""
        from app.database import database

        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setattr(database, "uses_postgres_app", lambda: False)

        with pytest.raises(ProductionDatabaseError):
            database.init_db()

    def test_development_startup_still_uses_sqlite(self, monkeypatch):
        from app.database import database

        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setattr(database, "uses_postgres_app", lambda: False)
        database.init_db()   # must not raise


# ---------------------------------------------------------------------------
# No route bypasses the policy
# ---------------------------------------------------------------------------

class TestNoRouteBypassesThePolicy:
    def test_application_code_uses_the_dialect_aware_connection(self):
        """Route modules must not reach for SQLite directly.

        get_app_connection() picks Postgres or SQLite per environment and runs
        the guard on the SQLite branch. A module calling get_sqlite_connection()
        directly would write to ephemeral storage in production.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in (root / "app").rglob("*.py"):
            if path.name == "database.py" or "__pycache__" in str(path):
                continue
            if "get_sqlite_connection" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path.relative_to(root)))
        assert offenders == [], f"these modules bypass the policy: {offenders}"
