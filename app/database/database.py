# =============================================================================
# CREDENT — Database Layer (Supabase Primary / SQLite Fallback)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# FORCE SUPABASE AS PRIMARY
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# FALLBACK SQLITE CONFIG (Using absolute path to avoid Windows reloader issues)
import sqlite3
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credent.db")

def _get_supabase() -> Client:
    # Print status for debugging startup
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ Supabase Credentials Missing in .env")
        return None
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return client
    except Exception as e:
        print(f"❌ Supabase Client Error: {e}")
        return None

def init_db():
    """Initializes local SQLite for fallback, and ensures Supabase is reachable."""
    # Local SQLite fallback init
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, name TEXT, sector TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS appraisal_records (id TEXT PRIMARY KEY, company_id TEXT, revenue REAL, debt REAL, base_score INTEGER, adjusted_score INTEGER, decision TEXT, recommended_loan_amount TEXT, recommended_interest_rate TEXT, decision_rationale TEXT, raw_document_data TEXT, integrity_flags TEXT, web_research TEXT, cam_report TEXT, financial_ratios TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    
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
        
    conn.commit()
    conn.close()
    
    sb = _get_supabase()
    if sb:
        print("✅ Supabase integration active.")
    else:
        print("⚠️ Supabase not configured. Using local SQLite.")

def save_appraisal(data):
    """Saves appraisal results to Supabase (primary) and SQLite (fallback)."""
    record_id = f"REC_{int(datetime.now().timestamp())}"
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
        "governance_assessment": data.get("governance_assessment") or {}
    }

    # 1. ATTEMPT SUPABASE SAVE
    if sb:
        try:
            sb.table("loan_applications").insert(payload).execute()
            print(f"✅ Saved appraisal to Supabase: {payload['borrower_name']}")
        except Exception as e:
            print(f"❌ Supabase Save Error: {e}")

    # 2. LOCAL SQLITE FALLBACK (Best practice for resilience)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO companies (id, name, sector) VALUES (?, ?, ?)', (data.get("company_id"), data.get("company_name"), data.get("sector")))
        cursor.execute('''INSERT INTO appraisal_records (id, company_id, revenue, debt, base_score, adjusted_score, decision, recommended_loan_amount, recommended_interest_rate, decision_rationale, raw_document_data, integrity_flags, web_research, cam_report, financial_ratios, management_score, promoter_analysis, governance_assessment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (record_id, data.get("company_id"), data.get("revenue"), data.get("debt"), data.get("base_score"), data.get("adjusted_score"), data.get("decision"), data.get("recommended_loan_amount"), data.get("recommended_interest_rate"), data.get("decision_rationale"), json.dumps(data.get("raw_document_data")), json.dumps(data.get("integrity_flags")), json.dumps(data.get("web_research")), json.dumps(data.get("cam_report")), json.dumps(payload["financial_ratios"]), payload["management_score"], json.dumps(payload["promoter_analysis"]), json.dumps(payload["governance_assessment"])))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ SQLite Save Error: {e}")

    return record_id

def get_recent_appraisals(limit=10):
    """Fetches records from Supabase if available, else SQLite."""
    sb = _get_supabase()
    
    if sb:
        try:
            response = sb.table("loan_applications").select("*").order("created_at", desc=True).limit(limit).execute()
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
                    "governance_assessment": item.get("governance_assessment", {})
                })
            return records
        except Exception as e:
            print(f"❌ Supabase Fetch Error: {e}")

    # Fallback to SQLite
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
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
            
        results.append(record)
    conn.close()
    return results

init_db()
