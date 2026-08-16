-- =============================================================================
-- ASE-52 Versioned DDL Migration: 001_ase52_schema_down.sql
-- Description: Rollback script for 001_ase52_schema.sql.
-- Drops all 8 ASE-52 schema tables in reverse foreign key order.
-- =============================================================================

DROP TABLE IF EXISTS outbox_events;
DROP TABLE IF EXISTS idempotency_records;
DROP TABLE IF EXISTS appraisal_results;
DROP TABLE IF EXISTS agent_executions;
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS cases;
DROP TABLE IF EXISTS tenants;
