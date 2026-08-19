-- Migration v28: Add http_callback_logs table for HTTP callback audit logging
-- Target: SQLite (engine.db)
-- Records every HTTP callback dispatch attempt (including retries) for audit
-- and troubleshooting. Each row = one fetch attempt to a callback URL.

CREATE TABLE IF NOT EXISTS http_callback_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  config_id VARCHAR(255) NOT NULL,
  config_name VARCHAR(255),
  callback_url VARCHAR(1024) NOT NULL,
  notify_event VARCHAR(64) NOT NULL,
  node_id VARCHAR(255),
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  request_body TEXT,
  request_headers TEXT,
  response_status_code INTEGER,
  response_body TEXT,
  duration_ms INTEGER,
  status VARCHAR(32) NOT NULL DEFAULT 'sent',
  error_message TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_http_callback_logs_flow ON http_callback_logs (flow_id);
CREATE INDEX IF NOT EXISTS idx_http_callback_logs_workflow ON http_callback_logs (workflow_id);
CREATE INDEX IF NOT EXISTS idx_http_callback_logs_status ON http_callback_logs (status);
CREATE INDEX IF NOT EXISTS idx_http_callback_logs_config ON http_callback_logs (config_id);