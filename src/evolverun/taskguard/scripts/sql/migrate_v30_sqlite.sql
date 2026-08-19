-- ============================================================================
-- Migration v30: Add engine column to flow_runs (SQLite)
-- Purpose:
--   Track which host platform (openclaw/claudecode/teclaw/hermes/cli) started
--   the workflow flow. Enables observability and filtering by engine in clawweb.
-- ============================================================================

ALTER TABLE flow_runs ADD COLUMN engine VARCHAR(255) DEFAULT NULL;