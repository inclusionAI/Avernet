-- ============================================================================
-- Migration v19: Add Langfuse correlation and observability fields to node_step_traces
-- Purpose:
--   1. session_key: embedded session key for Langfuse trace correlation
--   2. trace_id / observation_id: direct link to Langfuse records for deep navigation
--   3. model / latency_ms / prompt_tokens / completion_tokens: observability fields
--      aligned with observ-openclaw's semconv for display in clawweb diagnosis UI
-- ============================================================================

ALTER TABLE node_step_traces ADD COLUMN session_key VARCHAR(512) COMMENT '节点的embedded session key，用于关联aw_langfuse_traces';
ALTER TABLE node_step_traces ADD COLUMN trace_id VARCHAR(64) COMMENT 'Langfuse trace唯一标识';
ALTER TABLE node_step_traces ADD COLUMN observation_id VARCHAR(64) COMMENT 'Langfuse observation唯一标识';
ALTER TABLE node_step_traces ADD COLUMN model VARCHAR(255) COMMENT 'LLM模型名称（如GLM-5.1）';
ALTER TABLE node_step_traces ADD COLUMN latency_ms INTEGER COMMENT '步骤执行耗时（毫秒）';
ALTER TABLE node_step_traces ADD COLUMN prompt_tokens INTEGER COMMENT '输入token数';
ALTER TABLE node_step_traces ADD COLUMN completion_tokens INTEGER COMMENT '输出token数';