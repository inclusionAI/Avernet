-- ============================================================================
-- Migration v19: Add Langfuse correlation and observability fields to node_step_traces (SQLite)
-- ============================================================================

ALTER TABLE node_step_traces ADD COLUMN session_key VARCHAR(512);
ALTER TABLE node_step_traces ADD COLUMN trace_id VARCHAR(64);
ALTER TABLE node_step_traces ADD COLUMN observation_id VARCHAR(64);
ALTER TABLE node_step_traces ADD COLUMN model VARCHAR(255);
ALTER TABLE node_step_traces ADD COLUMN latency_ms INTEGER;
ALTER TABLE node_step_traces ADD COLUMN prompt_tokens INTEGER;
ALTER TABLE node_step_traces ADD COLUMN completion_tokens INTEGER;