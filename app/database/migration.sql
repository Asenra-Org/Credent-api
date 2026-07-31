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
