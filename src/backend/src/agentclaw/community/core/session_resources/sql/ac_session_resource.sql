-- Session resource table for a fresh MySQL-compatible deployment.
-- Existing deployments should use 2026_07_28_session_file_sharing.sql instead
-- of rerunning this create statement.

CREATE TABLE IF NOT EXISTS ac_session_resource (
  id BIGINT NOT NULL AUTO_INCREMENT,
  resource_id VARCHAR(128) NOT NULL,
  owner_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  scope_type VARCHAR(64) NOT NULL,
  scope_key_hash VARCHAR(128) NOT NULL,
  session_key_hash VARCHAR(128) NOT NULL,
  engine_type VARCHAR(64) NOT NULL,
  tenant VARCHAR(128) NOT NULL,
  bot_uuid VARCHAR(128) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  device_path VARCHAR(2048) NOT NULL,
  workspace_relative_path VARCHAR(2048) NOT NULL,
  transfer_id VARCHAR(256) NOT NULL,
  status VARCHAR(32) NOT NULL,
  transfer_api_version VARCHAR(32) NOT NULL DEFAULT 'bot_device_v1',
  session_key_ciphertext TEXT NULL,
  task_id VARCHAR(128) NULL,
  task_version INT NOT NULL DEFAULT 0,
  size_bytes BIGINT NULL,
  client_content_hash VARCHAR(128) NULL,
  materialized_ref_json TEXT NULL,
  error_code VARCHAR(128) NULL,
  deleted_at DATETIME NULL,
  gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_session_resource_resource_id (resource_id),
  KEY idx_session_resource_owner_bot_session
    (owner_id, bot_id, session_key_hash),
  KEY idx_session_resource_task (task_id, task_version)
) DEFAULT CHARSET = utf8mb4 COMMENT = 'TeamClaw session file resources';
