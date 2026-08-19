-- Migration v13: Add session_id to flow_control_slots for session-liveness-based zombie detection.
-- Target: MySQL / ZDAS (OceanBase)
-- BUG-10: The zombie cleanup previously used gmt_modified < now - 600s to detect dead flows,
-- but embedded-agent nodes calling LLM can run 5-15 minutes without any DB write updating
-- gmt_modified, causing active flows to be falsely killed.
-- Now zombie detection checks if the owning session is still alive via sessions.json,
-- and session_id records which gateway session owns each slot at acquire time.
-- Compliance: indexed column uses VARCHAR(255) not TEXT, COMMENT on column,
--             ALTER TABLE ADD INDEX for ODC/OceanBase compatibility.

ALTER TABLE flow_control_slots
  ADD COLUMN session_id VARCHAR(255) DEFAULT NULL COMMENT 'Gateway session ID that owns this slot, for session-liveness zombie detection';

-- Use ALTER TABLE ADD INDEX instead of CREATE INDEX for ODC/OceanBase compatibility
ALTER TABLE flow_control_slots ADD INDEX idx_fc_slots_session_id (session_id);