"""Phase 2 - the schema migration is additive and idempotent.

``init_db()`` runs on every import of ``app.database.database``, which means it
runs on every process start. It must therefore be safe to run repeatedly against
a database that already has the Phase 2 shape, and it must never drop, rename or
backfill an existing column.
"""

import sqlite3

import pytest

from app.database.database import get_sqlite_connection, init_db


def columns_of(table):
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}
    finally:
        conn.close()


def table_exists(name):
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


class TestNewTables:
    @pytest.mark.parametrize("table", ["case_documents", "case_reviews"])
    def test_table_is_created(self, table):
        init_db()
        assert table_exists(table)

    def test_case_documents_has_the_expected_shape(self):
        init_db()
        assert {
            "id", "case_id", "tenant_id", "filename", "doc_type", "storage_path",
            "page_count", "size_bytes", "status", "error_code", "uploaded_by",
            "created_at", "updated_at",
        } <= columns_of("case_documents")

    def test_case_reviews_has_the_expected_shape(self):
        init_db()
        assert {
            "id", "case_id", "tenant_id", "actor_id", "action", "note", "created_at",
        } <= columns_of("case_reviews")

    def test_new_tables_are_tenant_scoped(self):
        """Every new table carries tenant_id so isolation can be enforced in SQL."""
        init_db()
        for table in ("case_documents", "case_reviews"):
            assert "tenant_id" in columns_of(table)


class TestAdditiveColumns:
    def test_loan_cases_gained_the_workspace_columns(self):
        init_db()
        assert {
            "borrower_name", "case_reference", "facility_type", "requested_amount",
            "created_by", "assigned_to", "submitted_at", "reviewed_by", "reviewed_at",
            "decision", "risk_grade", "analysis_status", "decision_allowed",
            "lifecycle_status", "appraisal_id",
        } <= columns_of("loan_cases")

    def test_loan_cases_kept_every_pre_existing_column(self):
        """Nothing the coordinator or worker relies on may be dropped."""
        init_db()
        assert {
            "case_id", "status", "current_step", "has_financials", "has_promoters",
            "institution_id", "input_data", "result_data", "error_message",
            "created_at", "updated_at",
        } <= columns_of("loan_cases")

    def test_appraisal_records_gained_case_id(self):
        init_db()
        assert "case_id" in columns_of("appraisal_records")

    def test_appraisal_records_kept_the_p0_2_provenance_columns(self):
        init_db()
        assert {
            "model_provider", "model_name", "model_version", "prompt_version",
            "agent_version", "temperature", "provider_request_id", "agent_provenance",
            "analysis_status", "degraded_components", "decision_allowed",
            "provenance_recorded_at",
        } <= columns_of("appraisal_records")


class TestIdempotency:
    def test_running_the_migration_repeatedly_is_safe(self):
        for _ in range(3):
            init_db()
        assert "borrower_name" in columns_of("loan_cases")
        assert table_exists("case_documents")

    def test_repeated_runs_do_not_change_the_column_set(self):
        init_db()
        before_cases = columns_of("loan_cases")
        before_appraisals = columns_of("appraisal_records")
        init_db()
        init_db()
        assert columns_of("loan_cases") == before_cases
        assert columns_of("appraisal_records") == before_appraisals

    def test_existing_rows_are_not_backfilled(self):
        """A pre-existing case must keep NULL for the new columns.

        Inventing a borrower name for a historical row would put fabricated data
        in front of a credit officer.
        """
        init_db()
        conn = get_sqlite_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO loan_cases (case_id, status, institution_id) "
                "VALUES ('migration-probe', 'COMPLETED', 'probe-tenant')"
            )
            conn.commit()
        finally:
            conn.close()

        init_db()

        conn = get_sqlite_connection()
        try:
            row = conn.execute(
                "SELECT borrower_name, requested_amount, assigned_to, lifecycle_status "
                "FROM loan_cases WHERE case_id = 'migration-probe'"
            ).fetchone()
        finally:
            conn.close()
        assert row == (None, None, None, None)

        conn = get_sqlite_connection()
        try:
            conn.execute("DELETE FROM loan_cases WHERE case_id = 'migration-probe'")
            conn.commit()
        finally:
            conn.close()


class TestAuditTriggersSurvive:
    def test_append_only_triggers_are_still_installed(self):
        init_db()
        conn = get_sqlite_connection()
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "prevent_audit_logs_update" in names
        assert "prevent_audit_logs_delete" in names

    def test_indexes_added_by_phase_2_exist(self):
        init_db()
        conn = get_sqlite_connection()
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert {
            "idx_loan_cases_institution",
            "idx_loan_cases_created_at",
            "idx_appraisal_case_id",
            "idx_case_documents_case",
        } <= names
