-- Migration v23: ClawFlow: human intervention - credentials and session tracking columns on flow_runs.
-- Target: MySQL / ZDAS (OceanBase)
-- These columns store BaaS session context collected at workflow start time,
-- enabling the intervention feature to send messages back to the originating bot session.
-- Compliance: gmt_create/gmt_modified already exists on flow_runs.

ALTER TABLE flow_runs
  ADD COLUMN credentials_json TEXT COMMENT 'Parsed .credentials content (BOT_ID, OWNER_ID etc.) stored as JSON',
  ADD COLUMN origin_session_key VARCHAR(512) COMMENT 'SessionKey at workflow start (e.g. agent:main:dashboard:xxx-yyy)',
  ADD COLUMN origin_session_id VARCHAR(255) COMMENT 'Resolved session UUID from sessionKey',
  ADD COLUMN origin_bot_id VARCHAR(255) COMMENT 'BaaS-format bot_id {BOT_ID}:{OWNER_ID} (e.g. default:151614)';

-- Index for querying flows by origin_bot_id (e.g. find all flows from a specific bot)
ALTER TABLE flow_runs ADD INDEX idx_flow_runs_origin_bot_id (origin_bot_id);