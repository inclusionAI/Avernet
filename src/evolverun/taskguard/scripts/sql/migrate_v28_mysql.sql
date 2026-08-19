-- Migration v28: Add http_callback_logs table for HTTP callback audit logging
-- Target: MySQL / ZDAS (OceanBase)
-- Records every HTTP callback dispatch attempt (including retries) for audit
-- and troubleshooting. Each row = one fetch attempt to a callback URL.
-- Compliance: gmt_create/gmt_modified use TIMESTAMP with CURRENT_TIMESTAMP.
-- ZDAS constraint: indexes MUST be defined inline in CREATE TABLE, not as separate CREATE INDEX.

CREATE TABLE IF NOT EXISTS http_callback_logs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  flow_id VARCHAR(255) NOT NULL COMMENT 'Flow execution instance ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT 'Workflow ID',
  config_id VARCHAR(255) NOT NULL COMMENT 'http_callback_configs.config_id',
  config_name VARCHAR(255) COMMENT 'Config display name (denormalized for query convenience)',
  callback_url VARCHAR(1024) NOT NULL COMMENT 'Actual URL the HTTP POST was sent to',
  notify_event VARCHAR(64) NOT NULL COMMENT 'Triggering event: workflow_started, node_succeeded, node_failed, node_skipped, workflow_succeeded, workflow_failed',
  node_id VARCHAR(255) COMMENT 'Node ID (NULL for workflow-level events)',
  attempt INT NOT NULL DEFAULT 0 COMMENT 'Attempt number (0=first try, 1=first retry, ...)',
  max_attempts INT NOT NULL DEFAULT 1 COMMENT 'Total attempts configured (1 + maxRetries)',
  request_body MEDIUMTEXT COMMENT 'Request body JSON (truncated to ~10KB)',
  request_headers TEXT COMMENT 'Request headers JSON (includes X-Callback-Timestamp, excludes secret)',
  response_status_code INT COMMENT 'HTTP response code (NULL = no response received)',
  response_body TEXT COMMENT 'Response body (truncated to ~4KB)',
  duration_ms INT COMMENT 'Time for this attempt in milliseconds',
  status VARCHAR(32) NOT NULL DEFAULT 'sent' COMMENT 'delivered (2xx) | failed (5xx/network error) | skipped (4xx non-retryable)',
  error_message TEXT COMMENT 'Error message on failure (network error, timeout, HTTP error)',
  gmt_create TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Creation time',
  gmt_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Last modification time',
  KEY idx_http_callback_logs_flow (flow_id),
  KEY idx_http_callback_logs_workflow (workflow_id),
  KEY idx_http_callback_logs_status (status),
  KEY idx_http_callback_logs_config (config_id)
) COMMENT='HTTP callback dispatch audit log — one row per fetch attempt';