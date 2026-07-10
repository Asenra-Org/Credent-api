-- Alter loan_applications table to add promoter governance scores and results
ALTER TABLE loan_applications
ADD COLUMN IF NOT EXISTS management_score REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS promoter_analysis JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS governance_assessment JSONB DEFAULT '{}'::jsonb;
