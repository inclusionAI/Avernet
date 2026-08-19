-- ============================================================================
-- Migration v18: Add embedded_session_key to node_executions (SQLite)
-- ============================================================================

ALTER TABLE node_executions ADD COLUMN embedded_session_key VARCHAR(512);