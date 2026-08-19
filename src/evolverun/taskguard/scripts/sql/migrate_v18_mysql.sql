-- ============================================================================
-- Migration v18: Add embedded_session_key to node_executions
-- Purpose: Store the derived embedded session key for embedded-agent nodes,
--          enabling Langfuse trace correlation via session_id.
-- Issue: node_executions.session_key stores the user's main session key,
--        but Langfuse traces are indexed by the embedded session key
--        (parent:embedded:nodeId:flowId). Without embedded_session_key,
--        clawweb cannot query Langfuse for a specific node's traces.
-- ============================================================================

ALTER TABLE node_executions ADD COLUMN embedded_session_key VARCHAR(512) COMMENT 'embedded-agent节点的派生session key，用于关联Langfuse trace';

-- Index for looking up nodes by embedded session key
-- Using prefix index (128) for OceanBase compatibility with long VARCHAR
-- CREATE INDEX idx_ne_embedded_sk ON node_executions (embedded_session_key(128));
ALTER TABLE node_executions ADD INDEX idx_ne_embedded_sk (embedded_session_key);
