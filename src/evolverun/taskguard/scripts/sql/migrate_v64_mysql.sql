-- ============================================================================
-- Migration v64: Add version column to node_executions for optimistic locking
-- Purpose:
--   Add a `version` column (BIGINT, DEFAULT 1, NOT NULL) to node_executions
--   so that updateCompletionByFlowNode can use optimistic locking:
--   UPDATE ... SET status = ?, version = version + 1 WHERE ... AND version = ?
--
--   Existing rows get version = 1.
-- ============================================================================

ALTER TABLE node_executions ADD COLUMN version bigint(20) DEFAULT '1' COMMENT '乐观锁版本号';