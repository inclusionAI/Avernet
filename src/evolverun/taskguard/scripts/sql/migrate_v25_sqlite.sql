-- Migration v25: Add http_callback_configs table for HTTP callback notification system
-- Target: SQLite (engine.db)
-- This table supports configurable HTTP POST callbacks during workflow execution
-- to notify external subsystems of workflow state changes.

CREATE TABLE IF NOT EXISTS http_callback_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  url VARCHAR(1024) NOT NULL,
  secret VARCHAR(1024),
  enabled INTEGER NOT NULL DEFAULT 1,
  notify_on TEXT NOT NULL,
  timeout_ms INTEGER NOT NULL DEFAULT 5000,
  max_retries INTEGER NOT NULL DEFAULT 2,
  retry_delay_ms INTEGER NOT NULL DEFAULT 1000,
  include_node_output INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_http_callback_configs_config_id ON http_callback_configs (config_id);
CREATE INDEX IF NOT EXISTS idx_http_callback_configs_workflow ON http_callback_configs (workflow_id);