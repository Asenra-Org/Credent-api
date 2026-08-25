# =============================================================================
# CREDENT — Database Layer (Supabase Primary / SQLite Fallback)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import uuid
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv
import sqlite3

load_dotenv()

# FORCE SUPABASE AS PRIMARY
from app.core.db_policy import assert_sqlite_permitted, enforce_database_policy

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# FALLBACK SQLITE CONFIG (Using absolute path to avoid Windows reloader issues)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credent.db")

def _get_supabase() -> Client:
    # Print status for debugging startup
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[WARN] Supabase Credentials Missing in .env")
        return None
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return client
    except Exception as e:
        print(f"[ERROR] Supabase Client Error: {e}")
        return None

def get_sqlite_connection(timeout: float = 30.0) -> sqlite3.Connection:
    # [P0-5] SQLite is development/test only. In production this raises rather
    # than silently writing lending records to ephemeral container storage.
    assert_sqlite_permitted("get_sqlite_connection")
    """Returns a local SQLite connection configured with WAL mode and busy_timeout for production concurrency."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn

# =============================================================================
# [P0-5] Persistent application store.
#
# The identity layer already solved this problem in app/database/auth_db.py:
# one connection factory that returns Postgres when a DSN is configured and
# SQLite otherwise, with the handful of dialect differences translated. The
# appraisal/case tables had no such path, so every case-tracking call opened
# SQLite - which the P0-5 guard refuses in production. That is why production
# startup crashed, and why the guard was (wrongly) disabled to stop the crash.
#
# This reuses the identity layer's translation machinery rather than inventing
# a second one, so the two cannot drift.
# =============================================================================

def app_database_url():
    """Postgres DSN for the application store, if one is configured.

    Deliberately the same resolver the identity store uses. Running the case
    tables and the identity tables on one Postgres instance keeps a case and the
    user who acted on it in the same database, which matters for audit.
    """
    from app.database.auth_db import auth_database_url

    return auth_database_url()


def uses_postgres_app() -> bool:
    return app_database_url() is not None


def get_app_connection(timeout: float = 30.0):
    """Connection to the application store (cases, appraisals, audit).

    Postgres when a DSN is configured; SQLite otherwise. The SQLite branch goes
    through the P0-5 guard, so in production an unconfigured deployment fails
    closed here rather than silently writing to ephemeral storage.

    Every existing caller passes SQLite-flavoured SQL. The returned Postgres
    connection translates it, so call sites did not have to be rewritten.
    """
    url = app_database_url()
    if url:
        import psycopg2

        from app.database.auth_db import _PgConnection

        conn = psycopg2.connect(url, connect_timeout=int(timeout))
        conn.autocommit = False
        return _PgConnection(conn)

    return get_sqlite_connection(timeout=timeout)


# Postgres DDL mirroring the SQLite application schema. Types are chosen so the
# existing Python needs no changes: integer flags stay integers, ids stay text,
# and JSON payloads stay TEXT because the code json.dumps/loads them itself.
POSTGRES_APP_DDL = (
    """
    CREATE TABLE IF NOT EXISTS companies (
        id TEXT PRIMARY KEY,
        name TEXT,
        sector TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS appraisal_records (
        id TEXT PRIMARY KEY,
        company_id TEXT,
        revenue DOUBLE PRECISION,
        debt DOUBLE PRECISION,
        base_score INTEGER,
        adjusted_score INTEGER,
        decision TEXT,
        recommended_loan_amount TEXT,
        recommended_interest_rate TEXT,
        decision_rationale TEXT,
        raw_document_data TEXT,
        integrity_flags TEXT,
        web_research TEXT,
        cam_report TEXT,
        financial_ratios TEXT,
        management_score DOUBLE PRECISION DEFAULT 0.0,
        promoter_analysis TEXT DEFAULT '[]',
        governance_assessment TEXT DEFAULT '{}',
        institution_id TEXT DEFAULT 'DEFAULT',
        override_reason TEXT,
        is_override INTEGER DEFAULT 0,
        case_id TEXT,
        model_provider TEXT,
        model_name TEXT,
        model_version TEXT,
        prompt_version TEXT,
        agent_version TEXT,
        temperature DOUBLE PRECISION,
        provider_request_id TEXT,
        agent_provenance TEXT,
        analysis_status TEXT,
        degraded_components TEXT,
        decision_allowed INTEGER,
        provenance_recorded_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS loan_cases (
        case_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'PENDING',
        current_step TEXT DEFAULT 'init',
        has_financials INTEGER DEFAULT 1,
        has_promoters INTEGER DEFAULT 1,
        institution_id TEXT DEFAULT 'DEFAULT',
        input_data TEXT DEFAULT '{}',
        result_data TEXT DEFAULT '{}',
        error_message TEXT,
        borrower_name TEXT,
        case_reference TEXT,
        facility_type TEXT,
        requested_amount DOUBLE PRECISION,
        created_by TEXT,
        assigned_to TEXT,
        submitted_at TIMESTAMP,
        reviewed_by TEXT,
        reviewed_at TIMESTAMP,
        decision TEXT,
        risk_grade TEXT,
        analysis_status TEXT,
        decision_allowed INTEGER,
        lifecycle_status TEXT,
        appraisal_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_documents (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        doc_type TEXT,
        storage_path TEXT,
        page_count INTEGER,
        size_bytes INTEGER,
        status TEXT NOT NULL DEFAULT 'PENDING',
        error_code TEXT,
        uploaded_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_reviews (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS institution_policies (
        institution_id TEXT PRIMARY KEY,
        current_ratio_safe DOUBLE PRECISION,
        current_ratio_min DOUBLE PRECISION,
        dscr_safe DOUBLE PRECISION,
        dscr_min DOUBLE PRECISION,
        de_high DOUBLE PRECISION,
        auto_approve_cutoff DOUBLE PRECISION,
        auto_reject_cutoff DOUBLE PRECISION,
        penalty_weights TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        case_id TEXT,
        action TEXT NOT NULL,
        resource_type TEXT,
        resource_id TEXT,
        previous_state TEXT,
        new_state TEXT,
        decision TEXT,
        reason TEXT,
        sequence_number INTEGER NOT NULL,
        previous_hash TEXT NOT NULL,
        current_hash TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_chain_heads (
        tenant_id TEXT PRIMARY KEY,
        latest_sequence INTEGER NOT NULL DEFAULT 0,
        latest_hash TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_logs_tenant_seq ON audit_logs(tenant_id, sequence_number)",
    "CREATE INDEX IF NOT EXISTS idx_appraisal_created_at ON appraisal_records(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_appraisal_case_id ON appraisal_records(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_loan_cases_status ON loan_cases(status)",
    "CREATE INDEX IF NOT EXISTS idx_loan_cases_institution ON loan_cases(institution_id)",
    "CREATE INDEX IF NOT EXISTS idx_loan_cases_created_at ON loan_cases(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_loan_cases_assigned ON loan_cases(assigned_to)",
    "CREATE INDEX IF NOT EXISTS idx_case_documents_case ON case_documents(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_documents_tenant ON case_documents(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_reviews_case ON case_reviews(case_id)",
)

# The SQLite schema makes audit_logs append-only with BEFORE UPDATE/DELETE
# triggers. Postgres needs the equivalent, or the hash chain would be
# tamper-evident in development and merely tamper-*detectable* in production.
POSTGRES_AUDIT_APPEND_ONLY = (
    """
    CREATE OR REPLACE FUNCTION cresem_audit_logs_append_only()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_logs are append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS prevent_audit_logs_update ON audit_logs",
    """
    CREATE TRIGGER prevent_audit_logs_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION cresem_audit_logs_append_only()
    """,
    "DROP TRIGGER IF EXISTS prevent_audit_logs_delete ON audit_logs",
    """
    CREATE TRIGGER prevent_audit_logs_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION cresem_audit_logs_append_only()
    """,
)


def init_app_schema() -> bool:
    """Create the application tables in Postgres. No-op when using SQLite.

    Safe to run repeatedly: every statement is IF NOT EXISTS or CREATE OR
    REPLACE, and no existing row is altered or removed.
    """
    if not uses_postgres_app():
        return False

    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        for statement in POSTGRES_APP_DDL:
            cursor.execute(statement)
        for statement in POSTGRES_AUDIT_APPEND_ONLY:
            cursor.execute(statement)

        # Seed the default policy row only when absent, matching SQLite.
        cursor.execute("SELECT 1 FROM institution_policies WHERE institution_id = ?", ('DEFAULT',))
        if not cursor.fetchone():
            cursor.execute(
                """INSERT INTO institution_policies
                   (institution_id, current_ratio_safe, current_ratio_min, dscr_safe,
                    dscr_min, de_high, auto_approve_cutoff, auto_reject_cutoff, penalty_weights)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ('DEFAULT', 1.2, 1.0, 1.25, 1.0, 2.0, 60.0, 40.0,
                 json.dumps({"integrity_mismatch": 15.0, "promoter_flags": 10.0})),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def init_db():
    """Initialise the application store for the current environment.

    This runs at module import, which is what made the P0-5 guard fatal: it
    opened SQLite unconditionally, so importing this module in production raised
    ProductionDatabaseError and the application never started.

    It now branches. With Postgres configured the persistent schema is created
    and SQLite is never touched, so production startup does not require it. In
    development and test the existing SQLite bootstrap runs exactly as before.
    In production without a persistent database the guard fires and startup
    fails closed, which is the intended P0-5 behaviour.
    """
    if uses_postgres_app():
        init_app_schema()
        print("[STARTUP] Application store: Postgres schema verified.")
        sb = _get_supabase()
        if not sb:
            enforce_database_policy(SUPABASE_URL, SUPABASE_KEY)
        return

    # Development / test: SQLite. assert_sqlite_permitted() inside
    # get_app_connection() refuses this path in production.
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, name TEXT, sector TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS appraisal_records (id TEXT PRIMARY KEY, company_id TEXT, revenue REAL, debt REAL, base_score INTEGER, adjusted_score INTEGER, decision TEXT, recommended_loan_amount TEXT, recommended_interest_rate TEXT, decision_rationale TEXT, raw_document_data TEXT, integrity_flags TEXT, web_research TEXT, cam_report TEXT, financial_ratios TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')

    # [Added] Initialize institution_policies table
    cursor.execute('''CREATE TABLE IF NOT EXISTS institution_policies (
        institution_id TEXT PRIMARY KEY,
        current_ratio_safe REAL,
        current_ratio_min REAL,
        dscr_safe REAL,
        dscr_min REAL,
        de_high REAL,
        auto_approve_cutoff REAL,
        auto_reject_cutoff REAL,
        penalty_weights TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # [Added] Seed default policy row if empty
    cursor.execute('SELECT 1 FROM institution_policies WHERE institution_id = ?', ('DEFAULT',))
    if not cursor.fetchone():
        cursor.execute('''INSERT INTO institution_policies
            (institution_id, current_ratio_safe, current_ratio_min, dscr_safe, dscr_min, de_high, auto_approve_cutoff, auto_reject_cutoff, penalty_weights)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            ('DEFAULT', 1.2, 1.0, 1.25, 1.0, 2.0, 60.0, 40.0, json.dumps({
                "integrity_mismatch": 15.0,
                "promoter_flags": 10.0
            })))

    # Safe backward-compatible migration for existing local databases
    cursor.execute('PRAGMA table_info(appraisal_records)')
    columns = [col[1] for col in cursor.fetchall()]

    if 'financial_ratios' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN financial_ratios TEXT')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    if 'management_score' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN management_score REAL DEFAULT 0.0')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    if 'promoter_analysis' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN promoter_analysis TEXT DEFAULT "[]"')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    if 'governance_assessment' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN governance_assessment TEXT DEFAULT "{}"')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    # ------------------------------------------------------------------
    # [P0-2] Decision provenance columns.
    # Additive only: existing rows keep NULL, which is the correct reading -
    # provenance for historical appraisals is genuinely unknown and must never
    # be backfilled with invented model or prompt versions.
    # ------------------------------------------------------------------
    for _col, _type in (
        ('model_provider', 'TEXT'),
        ('model_name', 'TEXT'),
        ('model_version', 'TEXT'),
        ('prompt_version', 'TEXT'),
        ('agent_version', 'TEXT'),
        ('temperature', 'REAL'),
        ('provider_request_id', 'TEXT'),
        ('agent_provenance', 'TEXT'),
        ('analysis_status', 'TEXT'),
        ('degraded_components', 'TEXT'),
        ('decision_allowed', 'INTEGER'),
        ('provenance_recorded_at', 'TIMESTAMP'),
    ):
        if _col not in columns:
            try:
                cursor.execute(f'ALTER TABLE appraisal_records ADD COLUMN {_col} {_type}')
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e).lower():
                    raise e
    # [Added] Alter table to include institution_id column if not exists
    if 'institution_id' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN institution_id TEXT DEFAULT "DEFAULT"')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    # [Added for ASE-61] Alter table to include override audit fields
    if 'override_reason' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN override_reason TEXT')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    if 'is_override' not in columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN is_override INTEGER DEFAULT 0')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise e

    # [Added for ASE-46] Secondary index on created_at for recent appraisal feeds
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_appraisal_created_at ON appraisal_records(created_at DESC)')

    # [Added for ASE-54] Persistent loan case state table for dynamic coordinator
    cursor.execute('''CREATE TABLE IF NOT EXISTS loan_cases (
        case_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'PENDING',
        current_step TEXT DEFAULT 'init',
        has_financials INTEGER DEFAULT 1,
        has_promoters INTEGER DEFAULT 1,
        institution_id TEXT DEFAULT 'DEFAULT',
        input_data TEXT DEFAULT '{}',
        result_data TEXT DEFAULT '{}',
        error_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loan_cases_status ON loan_cases(status)')

    # ------------------------------------------------------------------
    # [PHASE-2] Case workspace columns.
    #
    # Additive only. Every column is nullable with no backfill: a case that
    # predates this migration genuinely has no recorded borrower name or
    # assignee, and inventing one would put fabricated data in front of a
    # credit officer. NULL reads correctly as "not recorded".
    #
    # lifecycle_status holds an explicitly recorded review state (IN_REVIEW,
    # RETURNED, or a human decision). When NULL the state is derived from the
    # execution signals by app.core.case_status.derive_case_status.
    # ------------------------------------------------------------------
    cursor.execute('PRAGMA table_info(loan_cases)')
    _case_columns = [col[1] for col in cursor.fetchall()]
    for _col, _type in (
        ('borrower_name', 'TEXT'),
        ('case_reference', 'TEXT'),
        ('facility_type', 'TEXT'),
        ('requested_amount', 'REAL'),
        ('created_by', 'TEXT'),
        ('assigned_to', 'TEXT'),
        ('submitted_at', 'TIMESTAMP'),
        ('reviewed_by', 'TEXT'),
        ('reviewed_at', 'TIMESTAMP'),
        ('decision', 'TEXT'),
        ('risk_grade', 'TEXT'),
        ('analysis_status', 'TEXT'),
        ('decision_allowed', 'INTEGER'),
        ('lifecycle_status', 'TEXT'),
        ('appraisal_id', 'TEXT'),
    ):
        if _col not in _case_columns:
            try:
                cursor.execute(f'ALTER TABLE loan_cases ADD COLUMN {_col} {_type}')
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e).lower():
                    raise e

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loan_cases_institution ON loan_cases(institution_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loan_cases_created_at ON loan_cases(created_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loan_cases_assigned ON loan_cases(assigned_to)')

    # [PHASE-2] Link an appraisal record back to the case that produced it.
    # Without this an appraisal and its case share no key, so a case workspace
    # cannot find its own CAM.
    cursor.execute('PRAGMA table_info(appraisal_records)')
    _appraisal_columns = [col[1] for col in cursor.fetchall()]
    if 'case_id' not in _appraisal_columns:
        try:
            cursor.execute('ALTER TABLE appraisal_records ADD COLUMN case_id TEXT')
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e).lower():
                raise e
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_appraisal_case_id ON appraisal_records(case_id)')

    # [PHASE-2] Documents belonging to a case. Uploads previously existed only
    # as a storage_path string inside loan_cases.input_data, so nothing could
    # list them, show per-document extraction status, or retry one file.
    cursor.execute('''CREATE TABLE IF NOT EXISTS case_documents (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        doc_type TEXT,
        storage_path TEXT,
        page_count INTEGER,
        size_bytes INTEGER,
        status TEXT NOT NULL DEFAULT 'PENDING',
        error_code TEXT,
        uploaded_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_case_documents_case ON case_documents(case_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_case_documents_tenant ON case_documents(tenant_id)')

    # [PHASE-2] Reviewer actions that are not themselves decisions: returning a
    # case to the analyst, requesting information, or leaving a review note.
    # The decision itself continues to flow through the existing audited
    # update-status path.
    cursor.execute('''CREATE TABLE IF NOT EXISTS case_reviews (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_case_reviews_case ON case_reviews(case_id)')

    # =========================================================================
    # [ASE-60] Identity, RBAC & Audit System Foundation
    # =========================================================================
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
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
        mfa_enabled INTEGER DEFAULT 0
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE)')

    # [PHASE-4] Account activity for the platform user console. Additive and
    # nullable: an account that has never signed in since this column existed
    # genuinely has no last-login time, and NULL is the honest reading.
    cursor.execute('PRAGMA table_info(users)')
    _user_columns = [col[1] for col in cursor.fetchall()]
    if 'last_login_at' not in _user_columns:
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP')
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e).lower():
                raise e

    cursor.execute('''CREATE TABLE IF NOT EXISTS organizations (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS invitations (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        role TEXT NOT NULL,
        token_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS tenant_memberships (
        user_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        role TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, tenant_id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant ON tenant_memberships(tenant_id)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        refresh_token_hash TEXT NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        revoked_at TIMESTAMP,
        is_revoked INTEGER DEFAULT 0,
        ip_address TEXT,
        user_agent TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_refresh_hash ON sessions(refresh_token_hash)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        case_id TEXT,
        action TEXT NOT NULL,
        resource_type TEXT,
        resource_id TEXT,
        previous_state TEXT,
        new_state TEXT,
        decision TEXT,
        reason TEXT,
        sequence_number INTEGER NOT NULL,
        previous_hash TEXT NOT NULL,
        current_hash TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_logs_tenant_seq ON audit_logs(tenant_id, sequence_number)')

    cursor.execute('''CREATE TRIGGER IF NOT EXISTS prevent_audit_logs_update
        BEFORE UPDATE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs are append-only');
        END;
    ''')

    cursor.execute('''CREATE TRIGGER IF NOT EXISTS prevent_audit_logs_delete
        BEFORE DELETE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs are append-only');
        END;
    ''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS audit_chain_heads (
        tenant_id TEXT PRIMARY KEY,
        latest_sequence INTEGER NOT NULL DEFAULT 0,
        latest_hash TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS system_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        is_bootstrapped INTEGER NOT NULL DEFAULT 0
    )''')

    # Initialize system_state if empty
    cursor.execute('SELECT 1 FROM system_state WHERE id = 1')
    if not cursor.fetchone():
        cursor.execute('INSERT INTO system_state (id, is_bootstrapped) VALUES (1, 0)')

    conn.commit()
    conn.close()

    sb = _get_supabase()
    if sb:
        print("[OK] Supabase integration active.")
    else:
        # [P0-5] Fail fast in production; warn only outside it.
        enforce_database_policy(SUPABASE_URL, SUPABASE_KEY)
        print("[WARN] Supabase not configured. Using local SQLite.")


# =============================================================================
# [ASE-54] Loan Case State CRUD Helpers
# =============================================================================

def create_case(case_id: str, input_data: dict, institution_id: str = "DEFAULT") -> str:
    """Create a new loan case record in PENDING state. Returns case_id."""
    conn = get_app_connection()
    try:
        conn.execute(
            '''INSERT INTO loan_cases (case_id, status, current_step, institution_id, input_data, created_at, updated_at)
               VALUES (?, 'PENDING', 'init', ?, ?, ?, ?)''',
            (case_id, institution_id, json.dumps(input_data),
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()
    return case_id


def update_case_step(case_id: str, step: str, status: str = "RUNNING") -> None:
    """Persist the coordinator's current execution step and status."""
    conn = get_app_connection()
    try:
        conn.execute(
            '''UPDATE loan_cases SET current_step = ?, status = ?, updated_at = ? WHERE case_id = ?''',
            (step, status, datetime.now(timezone.utc).isoformat(), case_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_case_result(case_id: str, result_data: dict, status: str = "COMPLETED") -> None:
    """
    Persist result_data for a loan case.

    Behaviour:
      - STATUS_COMPLETED → sets current_step = 'done' (terminal state).
      - Any other status  → persists result_data without touching current_step,
        so the crash-recovery checkpoint written by update_case_step() is preserved.
        Used by [W7] early ingestion snapshot and agents intermediate snapshot.
    """
    conn = get_app_connection()
    try:
        if status == "COMPLETED":
            conn.execute(
                '''UPDATE loan_cases
                   SET result_data = ?, status = ?, current_step = 'done', updated_at = ?
                   WHERE case_id = ?''',
                (json.dumps(result_data), status, datetime.now(timezone.utc).isoformat(), case_id)
            )
        else:
            # Intermediate snapshot: do NOT overwrite current_step
            conn.execute(
                '''UPDATE loan_cases
                   SET result_data = ?, status = ?, updated_at = ?
                   WHERE case_id = ?''',
                (json.dumps(result_data), status, datetime.now(timezone.utc).isoformat(), case_id)
            )
        conn.commit()
    finally:
        conn.close()



def mark_case_failed(case_id: str, error_message: str) -> None:
    """Mark a case as FAILED with the error reason."""
    conn = get_app_connection()
    try:
        conn.execute(
            '''UPDATE loan_cases SET status = 'FAILED', error_message = ?, updated_at = ? WHERE case_id = ?''',
            (str(error_message)[:1000], datetime.now(timezone.utc).isoformat(), case_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_case_status(case_id: str, status: str, current_step: str = None) -> None:
    """
    Atomic status + step update for a loan case.
    Called by the appraisal_worker at every transition:
      PENDING → RUNNING → COMPLETED | PAUSED | REJECTED | RETRYING
    """
    conn = get_app_connection()
    try:
        if current_step:
            conn.execute(
                '''UPDATE loan_cases SET status = ?, current_step = ?, updated_at = ? WHERE case_id = ?''',
                (status, current_step, datetime.now(timezone.utc).isoformat(), case_id)
            )
        else:
            conn.execute(
                '''UPDATE loan_cases SET status = ?, updated_at = ? WHERE case_id = ?''',
                (status, datetime.now(timezone.utc).isoformat(), case_id)
            )
        conn.commit()
    finally:
        conn.close()


def get_case(case_id: str, tenant_id: str = None) -> dict | None:
    """Retrieve a loan case row by case_id. Returns None if not found or if tenant_id doesn't match."""
    conn = get_app_connection()
    try:
        query = '''SELECT case_id, status, current_step, has_financials, has_promoters,
                   institution_id, input_data, result_data, error_message, created_at, updated_at
                   FROM loan_cases WHERE case_id = ?'''
        params = [case_id]
        if tenant_id:
            query += " AND institution_id = ?"
            params.append(tenant_id)

        cursor = conn.execute(query, params)
        row = cursor.fetchone()
        if not row:
            return None
        keys = ["case_id", "status", "current_step", "has_financials", "has_promoters",
                "institution_id", "input_data", "result_data", "error_message", "created_at", "updated_at"]
        record = dict(zip(keys, row))
        record["input_data"] = json.loads(record["input_data"] or "{}")
        record["result_data"] = json.loads(record["result_data"] or "{}")
        record["has_financials"] = bool(record["has_financials"])
        record["has_promoters"] = bool(record["has_promoters"])
        return record
    finally:
        conn.close()


def save_appraisal(data):

    """Saves appraisal results to Supabase (primary) and SQLite (fallback)."""
    record_id = f"REC_{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}"
    sb = _get_supabase()

    # Helper to safely convert semi-structured AI data to numeric for DB
    def _safe_float(val):
        if not val: return 0.0
        try:
            # Handle currency strings like "10.5 Cr" or "₹50,000"
            clean = str(val).replace('₹', '').replace(',', '').replace('Cr', '').strip()
            return float(clean)
        except: return 0.0

    def _safe_int(val):
        try: return int(float(str(val).split()[0]))
        except: return 0

    # Define borrower identity from multi-source data
    borrower = data.get("company_name") or \
               data.get("raw_document_data", {}).get("company_name") or \
               data.get("cam_report", {}).get("company_name") or \
               "Unknown Entity"

    payload = {
        "borrower_name": borrower,
        "sector": data.get("sector") or data.get("raw_document_data", {}).get("sector", "N/A"),
        "loan_amount": _safe_float(data.get("revenue")),
        "base_score": _safe_int(data.get("base_score")),
        "adjusted_score": _safe_int(data.get("adjusted_score")),
        "decision": data.get("decision", "PENDING"),
        "recommended_loan_amount": str(data.get("recommended_loan_amount", "N/A")),
        "recommended_interest_rate": str(data.get("recommended_interest_rate", "N/A")),
        "decision_rationale": data.get("decision_rationale", ""),
        "cam_report": data.get("cam_report", {}),
        "web_research": data.get("web_research", {}),
        "integrity_flags": data.get("integrity_flags", {}),
        "raw_document_data": data.get("raw_document_data", {}),
        "financial_ratios": data.get("financial_ratios", {}),

        # New fields for promoter governance
        "management_score": _safe_float(data.get("management_score", 0.0)),
        "promoter_analysis": data.get("promoter_analysis") or [],
        "governance_assessment": data.get("governance_assessment") or {},
        "institution_id": data.get("institution_id", "DEFAULT"),
        "override_reason": data.get("override_reason"),
        "is_override": bool(data.get("is_override", False))
    }

    # 1. ATTEMPT SUPABASE SAVE
    if sb:
        try:
            sb.table("loan_applications").insert(payload).execute()
            # [P0-1] Borrower name is personal data; log the record identifier instead.
            print(f"[OK] Saved appraisal to Supabase | appraisal_id={record_id}")
        except Exception as e:
            print(f"[ERROR] Supabase Save Error: {e}")

    # 2. LOCAL SQLITE FALLBACK (Best practice for resilience)
    try:
        conn = get_app_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO companies (id, name, sector) VALUES (?, ?, ?)', (data.get("company_id"), data.get("company_name"), data.get("sector")))
        # [P0-2] Provenance is supplied by the caller. Absent values stay None and
        # persist as SQL NULL - never invented.
        _prov = data.get("provenance_summary") or {}
        _agent_prov = data.get("agent_provenance")
        _agent_prov_json = json.dumps(_agent_prov) if _agent_prov is not None else None
        _degraded = data.get("degraded_components")
        _degraded_json = json.dumps(_degraded) if _degraded is not None else None
        _decision_allowed = None if data.get("decision_allowed") is None else (1 if data.get("decision_allowed") else 0)
        cursor.execute('''INSERT INTO appraisal_records (id, company_id, revenue, debt, base_score, adjusted_score, decision, recommended_loan_amount, recommended_interest_rate, decision_rationale, raw_document_data, integrity_flags, web_research, cam_report, financial_ratios, management_score, promoter_analysis, governance_assessment, institution_id, case_id, override_reason, is_override, model_provider, model_name, model_version, prompt_version, agent_version, temperature, provider_request_id, agent_provenance, analysis_status, degraded_components, decision_allowed, provenance_recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (record_id, data.get("company_id"), data.get("revenue"), data.get("debt"), data.get("base_score"), data.get("adjusted_score"), data.get("decision"), data.get("recommended_loan_amount"), data.get("recommended_interest_rate"), data.get("decision_rationale"), json.dumps(data.get("raw_document_data")), json.dumps(data.get("integrity_flags")), json.dumps(data.get("web_research")), json.dumps(data.get("cam_report")), json.dumps(payload["financial_ratios"]), payload["management_score"], json.dumps(payload["promoter_analysis"]), json.dumps(payload["governance_assessment"]), payload["institution_id"], data.get("case_id"), data.get("override_reason"), 1 if data.get("is_override") else 0, _prov.get("provider"), _prov.get("model_name"), _prov.get("model_version"), _prov.get("prompt_version"), _prov.get("agent_version"), _prov.get("temperature"), _prov.get("provider_request_id"), _agent_prov_json, data.get("analysis_status"), _degraded_json, _decision_allowed, _prov.get("generated_at")))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] SQLite Save Error: {e}")

    return record_id

def get_recent_appraisals(limit=10, tenant_id: str = None):
    """Fetches records from Supabase if available, else SQLite.
    Enforces tenant isolation by filtering institution_id if tenant_id is provided."""
    sb = _get_supabase()

    if sb:
        try:
            query = sb.table("loan_applications").select("*").order("created_at", desc=True).limit(limit)
            if tenant_id:
                query = query.eq("institution_id", tenant_id)
            response = query.execute()
            # Map Supabase fields to internal dict format
            records = []
            for item in response.data:
                records.append({
                    "id": item["id"],
                    "company_name": item["borrower_name"],
                    "sector": item["sector"],
                    "base_score": item["base_score"],
                    "adjusted_score": item["adjusted_score"],
                    "decision": item["decision"],
                    "recommended_loan_amount": item["recommended_loan_amount"],
                    "recommended_interest_rate": item["recommended_interest_rate"],
                    "created_at": item["created_at"],
                    "cam_report": item["cam_report"],
                    "web_research": item["web_research"],
                    "financial_ratios": item.get("financial_ratios", {}),
                    "management_score": item.get("management_score", 0.0),
                    "promoter_analysis": item.get("promoter_analysis", []),
                    "governance_assessment": item.get("governance_assessment", {}),
                    "override_reason": item.get("override_reason"),
                    "is_override": bool(item.get("is_override", False))
                })
            return records
        except Exception as e:
            print(f"[ERROR] Supabase Fetch Error: {e}")

    # Fallback to SQLite
    conn = get_app_connection()
    cursor = conn.cursor()
    if tenant_id:
        cursor.execute('SELECT a.*, c.name as company_name FROM appraisal_records a JOIN companies c ON a.company_id = c.id WHERE a.institution_id = ? ORDER BY a.created_at DESC LIMIT ?', (tenant_id, limit))
    else:
        cursor.execute('SELECT a.*, c.name as company_name FROM appraisal_records a JOIN companies c ON a.company_id = c.id ORDER BY a.created_at DESC LIMIT ?', (limit,))
    columns = [column[0] for column in cursor.description]
    results = []
    for row in cursor.fetchall():
        record = dict(zip(columns, row))
        for field in ["raw_document_data", "integrity_flags", "web_research", "cam_report", "financial_ratios", "promoter_analysis", "governance_assessment"]:
            if record.get(field): record[field] = json.loads(record[field])

        if "sector" not in record:
            raw_data = record.get("raw_document_data") or {}
            record["sector"] = raw_data.get("sector", "N/A")

        if "is_override" in record and record["is_override"] is not None:
            record["is_override"] = bool(record["is_override"])

        results.append(record)
    conn.close()
    return results

def update_appraisal_status(
    appraisal_id: str,
    decision: str,
    rationale: str,
    tenant_id: str = None,
    override_reason: str = None,
    is_override: bool = False
) -> bool:
    """Updates status overrides in both Supabase (primary) and SQLite (fallback). Enforces tenant isolation."""
    sb = _get_supabase()
    status_map = {
        "APPROVE": "APPROVED",
        "REJECT": "REJECTED",
        "PENDING": "UNDER_REVIEW",
        "MANUAL": "UNDER_REVIEW"
    }
    final_status = status_map.get(decision, "UNDER_REVIEW")

    supabase_success = False
    if sb:
        try:
            update_payload = {
                "decision": decision,
                "status": final_status,
                "decision_rationale": rationale,
                "override_reason": override_reason,
                "is_override": is_override
            }
            query = sb.table("loan_applications").update(update_payload).eq("id", appraisal_id)
            if tenant_id:
                query = query.eq("institution_id", tenant_id)

            query.execute()
            print(f"[OK] Updated status in Supabase for {appraisal_id}")
            supabase_success = True
        except Exception as e:
            print(f"[ERROR] Supabase status update error: {e}")

    sqlite_success = False
    try:
        conn = get_app_connection()
        cursor = conn.cursor()

        if tenant_id:
            cursor.execute('''UPDATE appraisal_records
                SET decision = ?, decision_rationale = ?, override_reason = ?, is_override = ?
                WHERE id = ? AND institution_id = ?''', (decision, rationale, override_reason, 1 if is_override else 0, appraisal_id, tenant_id))
            if cursor.rowcount > 0:
                sqlite_success = True
        else:
            cursor.execute('''UPDATE appraisal_records
                SET decision = ?, decision_rationale = ?, override_reason = ?, is_override = ?
                WHERE id = ?''', (decision, rationale, override_reason, 1 if is_override else 0, appraisal_id))
            if cursor.rowcount > 0:
                sqlite_success = True

        conn.commit()
        conn.close()

        if sqlite_success:
            print(f"[OK] Updated status in SQLite for {appraisal_id}")
    except Exception as e:
        print(f"[ERROR] SQLite status update error: {e}")

    return supabase_success or sqlite_success

def get_policy(institution_id: str) -> dict:
    """Fetches policy parameters for an institution from SQLite fallback."""
    try:
        conn = get_app_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT institution_id, current_ratio_safe, current_ratio_min,
                                 dscr_safe, dscr_min, de_high, auto_approve_cutoff,
                                 auto_reject_cutoff, penalty_weights
                          FROM institution_policies WHERE institution_id = ?''', (institution_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "institution_id": row[0],
                "current_ratio_safe": row[1],
                "current_ratio_min": row[2],
                "dscr_safe": row[3],
                "dscr_min": row[4],
                "de_high": row[5],
                "auto_approve_cutoff": row[6],
                "auto_reject_cutoff": row[7],
                "penalty_weights": json.loads(row[8]) if row[8] else {}
            }
    except Exception as e:
        print(f"[ERROR] Error fetching policy: {e}")
    return None

def save_policy(policy_data: dict) -> bool:
    """Saves policy parameters for an institution to SQLite fallback."""
    try:
        conn = get_app_connection()
        cursor = conn.cursor()
        cursor.execute('''INSERT OR REPLACE INTO institution_policies
            (institution_id, current_ratio_safe, current_ratio_min, dscr_safe, dscr_min, de_high, auto_approve_cutoff, auto_reject_cutoff, penalty_weights)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                policy_data.get("institution_id"),
                policy_data.get("current_ratio_safe"),
                policy_data.get("current_ratio_min"),
                policy_data.get("dscr_safe"),
                policy_data.get("dscr_min"),
                policy_data.get("de_high"),
                policy_data.get("auto_approve_cutoff"),
                policy_data.get("auto_reject_cutoff"),
                json.dumps(policy_data.get("penalty_weights", {}))
            ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Error saving policy: {e}")
        return False

# =============================================================================
# [PHASE-2] Case listing, case detail and audit reads.
#
# Every function here takes tenant_id as a required argument and filters on it
# in SQL. Tenant scoping is not optional and is never inferred from a request
# body - the caller passes the value the access token was issued for.
# =============================================================================

_CASE_LIST_COLUMNS = (
    "case_id", "status", "current_step", "institution_id", "error_message",
    "created_at", "updated_at", "borrower_name", "case_reference",
    "facility_type", "requested_amount", "created_by", "assigned_to",
    "submitted_at", "reviewed_by", "reviewed_at", "decision", "risk_grade",
    "analysis_status", "decision_allowed", "lifecycle_status", "appraisal_id",
)


def _execution_signals(result_data_raw):
    """Pull the P0-4 gate signals out of a persisted result_data blob.

    The loan_cases columns analysis_status / decision_allowed / decision are
    written going forward, but every case that already exists carries these
    values inside result_data. Reading them back recovers data that was
    genuinely recorded - it does not guess. A row with neither source yields
    empty values, and the lifecycle deriver treats that as "not yet known".
    """
    if not result_data_raw:
        return {}
    try:
        payload = json.loads(result_data_raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    combined = payload.get("combined_decision")
    if not isinstance(combined, dict):
        combined = {}
    return {
        "analysis_status": payload.get("analysis_status"),
        "decision_allowed": payload.get("decision_allowed"),
        "decision": combined.get("decision"),
        "degraded_components": payload.get("degraded_components"),
        "missing_required": payload.get("missing_required"),
        "appraisal_id": payload.get("appraisal_id"),
    }


def _hydrate_case_row(row, columns):
    """Turn a loan_cases row into an API-shaped dict with a lifecycle status."""
    from app.core.case_status import derive_case_status

    record = dict(zip(columns, row))
    signals = _execution_signals(record.pop("_result_data", None))

    # Persisted columns win; result_data fills in only what the columns lack,
    # so a value written explicitly is never overwritten by an older snapshot.
    for key in ("analysis_status", "decision", "appraisal_id"):
        if record.get(key) in (None, "") and signals.get(key) is not None:
            record[key] = signals[key]
    if record.get("decision_allowed") is None and signals.get("decision_allowed") is not None:
        record["decision_allowed"] = 1 if signals["decision_allowed"] else 0

    if record.get("decision_allowed") is not None:
        record["decision_allowed"] = bool(record["decision_allowed"])

    record["degraded_components"] = signals.get("degraded_components") or []
    record["missing_required"] = signals.get("missing_required") or []
    record["lifecycle_status"] = derive_case_status(record).value
    return record


def list_cases(
    tenant_id,
    status=None,
    assigned_to=None,
    created_by=None,
    search=None,
    sort="created_at",
    direction="desc",
    limit=25,
    offset=0,
):
    """List cases for one tenant.

    Lifecycle status is derived per row rather than filtered in SQL, because it
    is computed from several columns plus result_data. Filtering therefore
    happens after hydration, and the reported total reflects the filtered set.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required for list_cases")

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    sortable = {"created_at", "updated_at", "borrower_name", "requested_amount", "submitted_at"}
    sort_col = sort if sort in sortable else "created_at"
    sort_dir = "ASC" if str(direction).lower() == "asc" else "DESC"

    where = ["institution_id = ?"]
    params = [tenant_id]
    if assigned_to:
        where.append("assigned_to = ?")
        params.append(assigned_to)
    if created_by:
        where.append("created_by = ?")
        params.append(created_by)
    if search:
        where.append("(LOWER(COALESCE(borrower_name, '')) LIKE ? OR LOWER(case_id) LIKE ? "
                     "OR LOWER(COALESCE(case_reference, '')) LIKE ?)")
        needle = "%" + str(search).lower() + "%"
        params.extend([needle, needle, needle])

    where_sql = " AND ".join(where)
    columns = _CASE_LIST_COLUMNS + ("_result_data",)
    select_sql = ", ".join(_CASE_LIST_COLUMNS) + ", result_data"

    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT " + select_sql + " FROM loan_cases WHERE " + where_sql
            + " ORDER BY " + sort_col + " " + sort_dir,
            params,
        )
        rows = [_hydrate_case_row(r, columns) for r in cursor.fetchall()]
    finally:
        conn.close()

    if status:
        wanted = {str(s).strip().upper() for s in status if str(s).strip()}
        if wanted:
            rows = [r for r in rows if r["lifecycle_status"] in wanted]

    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(page),
    }


def get_case_detail(case_id, tenant_id):
    """One case, tenant-scoped, with its lifecycle status and full result_data."""
    if not tenant_id:
        raise ValueError("tenant_id is required for get_case_detail")

    columns = _CASE_LIST_COLUMNS + ("_result_data",)
    select_sql = ", ".join(_CASE_LIST_COLUMNS) + ", result_data"

    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT " + select_sql + ", input_data, result_data FROM loan_cases "
            "WHERE case_id = ? AND institution_id = ?",
            (case_id, tenant_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        base = _hydrate_case_row(row[:len(columns)], columns)
        raw_input, raw_result = row[-2], row[-1]
    finally:
        conn.close()

    try:
        base["input_data"] = json.loads(raw_input) if raw_input else {}
    except (ValueError, TypeError):
        base["input_data"] = {}
    try:
        base["result_data"] = json.loads(raw_result) if raw_result else {}
    except (ValueError, TypeError):
        base["result_data"] = {}
    return base


def get_case_appraisal(case_id, tenant_id):
    """The appraisal record produced by a case, if one has been linked.

    Returns None when no appraisal is linked yet - the honest answer for a case
    that has not completed, and for historical cases written before
    appraisal_records.case_id existed.
    """
    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, decision, decision_rationale, adjusted_score, base_score,
                      cam_report, financial_ratios, analysis_status, decision_allowed,
                      degraded_components, model_provider, model_name, model_version,
                      prompt_version, agent_version, agent_provenance,
                      provenance_recorded_at, created_at
               FROM appraisal_records
               WHERE case_id = ? AND institution_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (case_id, tenant_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
    finally:
        conn.close()

    record = dict(zip(columns, row))
    for field in ("cam_report", "financial_ratios", "degraded_components", "agent_provenance"):
        if record.get(field):
            try:
                record[field] = json.loads(record[field])
            except (ValueError, TypeError):
                pass
    if record.get("decision_allowed") is not None:
        record["decision_allowed"] = bool(record["decision_allowed"])
    return record


def list_case_documents(case_id, tenant_id):
    """Documents attached to a case, tenant-scoped.

    storage_path is deliberately not selected: it is an internal object handle
    and has no business reaching a browser.
    """
    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, case_id, filename, doc_type, page_count, size_bytes,
                      status, error_code, uploaded_by, created_at, updated_at
               FROM case_documents
               WHERE case_id = ? AND tenant_id = ?
               ORDER BY created_at ASC""",
            (case_id, tenant_id),
        )
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, r)) for r in cursor.fetchall()]
    finally:
        conn.close()


def list_audit_events(
    tenant_id,
    case_id=None,
    user_id=None,
    action=None,
    resource_type=None,
    date_from=None,
    date_to=None,
    limit=50,
    offset=0,
):
    """Read the audit chain for one tenant.

    This is a read path only. The chain remains append-only - the SQLite
    triggers that refuse UPDATE and DELETE on audit_logs are untouched, and
    nothing here writes.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required for list_audit_events")

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where = ["tenant_id = ?"]
    params = [tenant_id]
    for column, value in (
        ("case_id", case_id),
        ("user_id", user_id),
        ("action", action),
        ("resource_type", resource_type),
    ):
        if value:
            where.append(column + " = ?")
            params.append(value)
    if date_from:
        where.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("timestamp <= ?")
        params.append(date_to)

    where_sql = " AND ".join(where)

    conn = get_app_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE " + where_sql, params)
        total = cursor.fetchone()[0]

        cursor.execute(
            """SELECT id, tenant_id, user_id, case_id, action, resource_type,
                      resource_id, previous_state, new_state, decision, reason,
                      sequence_number, previous_hash, current_hash, timestamp
               FROM audit_logs WHERE """ + where_sql
            + " ORDER BY sequence_number DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        columns = [c[0] for c in cursor.description]
        items = []
        for row in cursor.fetchall():
            record = dict(zip(columns, row))
            for field in ("previous_state", "new_state"):
                if record.get(field):
                    try:
                        record[field] = json.loads(record[field])
                    except (ValueError, TypeError):
                        pass
            items.append(record)
    finally:
        conn.close()

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
    }


def link_case_appraisal(
    case_id,
    appraisal_id,
    analysis_status=None,
    decision_allowed=None,
    decision=None,
    borrower_name=None,
):
    """Record which appraisal a case produced, plus the P0-4 gate outcome.

    Called once an appraisal has been persisted. Only values that were actually
    supplied are written - a None argument leaves the existing column untouched
    rather than blanking it, so a partial call cannot erase a recorded result.

    Failure here is logged and swallowed: the appraisal itself is already
    durable, and losing the convenience link must not fail an otherwise
    successful run. The link can be recovered from appraisal_records.case_id.
    """
    if not case_id or not appraisal_id:
        return False

    assignments = ["appraisal_id = ?"]
    params = [appraisal_id]
    for column, value in (
        ("analysis_status", analysis_status),
        ("decision", decision),
        ("borrower_name", borrower_name),
    ):
        if value is not None:
            assignments.append(column + " = ?")
            params.append(value)
    if decision_allowed is not None:
        assignments.append("decision_allowed = ?")
        params.append(1 if decision_allowed else 0)

    assignments.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(case_id)

    try:
        conn = get_app_connection()
        try:
            conn.execute(
                "UPDATE loan_cases SET " + ", ".join(assignments) + " WHERE case_id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] link_case_appraisal failed for case_id={case_id}: {type(e).__name__}")
        return False


init_db()
