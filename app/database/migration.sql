-- Alter loan_applications table to add promoter governance scores and results
ALTER TABLE loan_applications
ADD COLUMN IF NOT EXISTS management_score REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS promoter_analysis JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS governance_assessment JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS institution_id TEXT DEFAULT 'DEFAULT';

-- Create institution_policies table for Supabase
CREATE TABLE IF NOT EXISTS institution_policies (
    institution_id TEXT PRIMARY KEY,
    current_ratio_safe REAL,
    current_ratio_min REAL,
    dscr_safe REAL,
    dscr_min REAL,
    de_high REAL,
    auto_approve_cutoff REAL,
    auto_reject_cutoff REAL,
    penalty_weights JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- [AI-A-W7] Persistent loan case state table (mirrors SQLite loan_cases schema)
-- Required for production Supabase deployment.
-- The SQLite equivalent is created automatically by init_db() in database.py.
-- =============================================================================
CREATE TABLE IF NOT EXISTS loan_cases (
    case_id        TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'PENDING',
    current_step   TEXT DEFAULT 'init',
    has_financials BOOLEAN DEFAULT TRUE,
    has_promoters  BOOLEAN DEFAULT TRUE,
    institution_id TEXT DEFAULT 'DEFAULT',
    input_data     JSONB DEFAULT '{}',
    result_data    JSONB DEFAULT '{}',
    error_message  TEXT,
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for efficient status-based polling (GET /ingest/status/{case_id} + worker queues)
CREATE INDEX IF NOT EXISTS idx_loan_cases_status ON loan_cases(status);
CREATE INDEX IF NOT EXISTS idx_loan_cases_institution ON loan_cases(institution_id);

