-- Migration v23: ClawFlow: human intervention - credentials and session tracking columns on flow_runs.
-- Target: SQLite (engine.db)
-- These columns store BaaS session context collected at workflow start time,
-- enabling the intervention feature to send messages back to the originating bot session.

ALTER TABLE flow_runs ADD COLUMN credentials_json TEXT;
ALTER TABLE flow_runs ADD COLUMN origin_session_key VARCHAR(512);
ALTER TABLE flow_runs ADD COLUMN origin_session_id VARCHAR(255);
ALTER TABLE flow_runs ADD COLUMN origin_bot_id VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_flow_runs_origin_bot_id ON flow_runs (origin_bot_id);