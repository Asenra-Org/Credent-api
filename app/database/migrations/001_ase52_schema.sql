-- =============================================================================
-- ASE-52 Versioned DDL Migration: 001_ase52_schema.sql
-- Description: Establishes the authoritative 8-table relational database schema
-- for the Credent Enterprise AI Credit Appraisal Platform.
-- Multi-instance safe, fail-closed release identity, canonical state machines.
-- =============================================================================

-- 1. Tenants Table
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed Default System Tenant for single-tenant / local mode compatibility
INSERT INTO tenants (id, name, status)
VALUES ('DEFAULT', 'Default Enterprise Tenant', 'ACTIVE')
ON CONFLICT (id) DO NOTHING;

-- 2. Cases Table
CREATE TABLE IF NOT EXISTS cases (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    borrower_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('INITIATED', 'QUEUED', 'PROCESSING', 'PAUSED', 'COMPLETED', 'FAILED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cases_tenant_status ON cases(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cases_tenant_created ON cases(tenant_id, created_at DESC);

-- 3. Documents Table
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(64) PRIMARY KEY,
    case_id VARCHAR(64) NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    filename VARCHAR(255) NOT NULL,
    storage_key VARCHAR(512) NOT NULL,
    doc_role VARCHAR(32) NOT NULL CHECK (doc_role IN ('REQUIRED', 'OPTIONAL')),
    status VARCHAR(32) NOT NULL CHECK (status IN ('UPLOAD_INTENT', 'UPLOADING', 'UPLOAD_COMPLETE', 'DB_REGISTERED', 'ACTIVE', 'RETENTION_LOCKED', 'DELETABLE', 'DELETED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_case_id ON documents(case_id);
CREATE INDEX IF NOT EXISTS idx_documents_tenant_status ON documents(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_orphan_sweep ON documents(status, created_at) WHERE status = 'UPLOADING';

-- 4. Jobs Table
CREATE TABLE IF NOT EXISTS jobs (
    id VARCHAR(64) PRIMARY KEY,
    case_id VARCHAR(64) NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    celery_task_id VARCHAR(128),
    release_tag VARCHAR(64) NOT NULL,
    stage_name VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED', 'SKIPPED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_case_id ON jobs(case_id);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant_release ON jobs(tenant_id, release_tag);
CREATE INDEX IF NOT EXISTS idx_jobs_celery_task ON jobs(celery_task_id);

-- 5. Agent Executions Table
CREATE TABLE IF NOT EXISTS agent_executions (
    id VARCHAR(64) PRIMARY KEY,
    job_id VARCHAR(64) NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    celery_task_id VARCHAR(128),
    release_tag VARCHAR(64) NOT NULL,
    agent_type VARCHAR(64) NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1 AND attempt_number <= 4),
    prompt_hash VARCHAR(64),
    status VARCHAR(32) NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'UNKNOWN_EXTERNAL_OUTCOME', 'RESULT_RECOVERABLE', 'RETRY_AUTHORIZED', 'SUCCESS', 'FAILED', 'TIMED_OUT', 'SKIPPED')),
    output_storage_key VARCHAR(512),
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_agent_exec_job_id ON agent_executions(job_id);
CREATE INDEX IF NOT EXISTS idx_agent_exec_tenant_release ON agent_executions(tenant_id, release_tag);

-- 6. Appraisal Results Table
CREATE TABLE IF NOT EXISTS appraisal_results (
    id VARCHAR(64) PRIMARY KEY,
    case_id VARCHAR(64) NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    base_score INTEGER,
    adjusted_score INTEGER,
    decision VARCHAR(32) NOT NULL,
    cam_report_storage_key VARCHAR(512),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_appraisal_results_case_id ON appraisal_results(case_id);
CREATE INDEX IF NOT EXISTS idx_appraisal_results_tenant_id ON appraisal_results(tenant_id);

-- 7. Idempotency Records Table
CREATE TABLE IF NOT EXISTS idempotency_records (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    idempotency_key VARCHAR(255) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    response_payload TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_idempotency_tenant_key UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_tenant_key ON idempotency_records(tenant_id, idempotency_key);

-- 8. Outbox Events Table
CREATE TABLE IF NOT EXISTS outbox_events (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'FAILED', 'DEAD_LETTERED')),
    deduplication_key VARCHAR(128),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    lease_until TIMESTAMP WITH TIME ZONE,
    published_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    release_tag VARCHAR(64) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbox_poll ON outbox_events(status, available_at) WHERE status IN ('PENDING', 'CLAIMED');
CREATE INDEX IF NOT EXISTS idx_outbox_tenant_aggregate ON outbox_events(tenant_id, aggregate_type, aggregate_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_dedup ON outbox_events(tenant_id, aggregate_type, aggregate_id, event_type, deduplication_key) WHERE deduplication_key IS NOT NULL;
