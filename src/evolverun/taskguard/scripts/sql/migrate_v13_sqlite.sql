-- Migration v13: Add session_id to flow_control_slots for session-liveness-based zombie detection.
-- Target: SQLite (engine.db)
-- BUG-10: The zombie cleanup previously used gmt_modified < now - 600s to detect dead flows,
-- but embedded-agent nodes calling LLM can run 5-15 minutes without any DB write updating
-- gmt_modified, causing active flows to be falsely killed.
-- Now zombie detection checks if the owning session is still alive via sessions.json,
-- and session_id records which gateway session owns each slot at acquire time.

ALTER TABLE flow_control_slots ADD COLUMN session_id VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_fc_slots_session_id ON flow_control_slots (session_id);