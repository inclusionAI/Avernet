-- ============================================================================
-- Migration v64: Add version column to node_executions for optimistic locking
-- Purpose:
--   Add a `version` column (BIGINT, DEFAULT 1, NOT NULL) to node_executions
--   so that updateCompletionByFlowNode can use optimistic locking:
--   UPDATE ... SET status = ?, version = version + 1 WHERE ... AND version = ?
--
--   Existing rows get version = 1.
-- ============================================================================

-- SQLite requires recreating the table to add a column with a non-NULL default.
-- Since SQLite 3.25.0+ supports ALTER TABLE ADD COLUMN with DEFAULT for
-- non-NULL columns, we use the simpler syntax.
ALTER TABLE node_executions ADD COLUMN version INTEGER NOT NULL DEFAULT 1;