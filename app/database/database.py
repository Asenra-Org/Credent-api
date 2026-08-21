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
    """Returns a local SQLite connection configured with WAL mode and busy_timeout for production concurrency."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn

def init_db():
    """Initializes local SQLite for fallback, and ensures Supabase is reachable."""
    # Local SQLite fallback init
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
        print("[WARN] Supabase not configured. Using local SQLite.")


# =============================================================================
# [ASE-54] Loan Case State CRUD Helpers
# =============================================================================

def create_case(case_id: str, input_data: dict, institution_id: str = "DEFAULT") -> str:
    """Create a new loan case record in PENDING state. Returns case_id."""
    conn = get_sqlite_connection()
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
    conn = get_sqlite_connection()
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
    conn = get_sqlite_connection()
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
    conn = get_sqlite_connection()
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
    conn = get_sqlite_connection()
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
    conn = get_sqlite_connection()
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
            print(f"[OK] Saved appraisal to Supabase: {payload['borrower_name']}")
        except Exception as e:
            print(f"[ERROR] Supabase Save Error: {e}")

    # 2. LOCAL SQLITE FALLBACK (Best practice for resilience)
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO companies (id, name, sector) VALUES (?, ?, ?)', (data.get("company_id"), data.get("company_name"), data.get("sector")))
        cursor.execute('''INSERT INTO appraisal_records (id, company_id, revenue, debt, base_score, adjusted_score, decision, recommended_loan_amount, recommended_interest_rate, decision_rationale, raw_document_data, integrity_flags, web_research, cam_report, financial_ratios, management_score, promoter_analysis, governance_assessment, institution_id, override_reason, is_override) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (record_id, data.get("company_id"), data.get("revenue"), data.get("debt"), data.get("base_score"), data.get("adjusted_score"), data.get("decision"), data.get("recommended_loan_amount"), data.get("recommended_interest_rate"), data.get("decision_rationale"), json.dumps(data.get("raw_document_data")), json.dumps(data.get("integrity_flags")), json.dumps(data.get("web_research")), json.dumps(data.get("cam_report")), json.dumps(payload["financial_ratios"]), payload["management_score"], json.dumps(payload["promoter_analysis"]), json.dumps(payload["governance_assessment"]), payload["institution_id"], data.get("override_reason"), 1 if data.get("is_override") else 0))
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
    conn = get_sqlite_connection()
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
        conn = get_sqlite_connection()
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
        conn = get_sqlite_connection()
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
        conn = get_sqlite_connection()
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

init_db()
