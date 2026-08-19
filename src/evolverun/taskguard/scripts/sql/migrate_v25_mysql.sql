-- Migration v25: Add http_callback_configs table for HTTP callback notification system
-- Target: MySQL / ZDAS (OceanBase)
-- This table supports configurable HTTP POST callbacks during workflow execution
-- to notify external subsystems of workflow state changes.
-- Compliance: gmt_create/gmt_modified use TIMESTAMP with CURRENT_TIMESTAMP.
-- ZDAS constraint: indexes MUST be defined inline in CREATE TABLE, not as separate CREATE INDEX.

CREATE TABLE IF NOT EXISTS http_callback_configs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  config_id VARCHAR(255) NOT NULL COMMENT 'Unique config identifier (e.g. hcb_xxx)',
  workflow_id VARCHAR(255) NOT NULL COMMENT 'Workflow ID this config belongs to',
  name VARCHAR(255) NOT NULL COMMENT 'Human-readable name',
  url VARCHAR(1024) NOT NULL COMMENT 'Target HTTPS URL for callbacks',
  secret VARCHAR(1024) COMMENT 'HMAC-SHA256 signing secret (optional)',
  enabled TINYINT NOT NULL DEFAULT 1 COMMENT '1=enabled, 0=disabled',
  notify_on TEXT NOT NULL COMMENT 'JSON array of NotifyEvent values',
  timeout_ms INT NOT NULL DEFAULT 5000 COMMENT 'HTTP request timeout in ms',
  max_retries INT NOT NULL DEFAULT 2 COMMENT 'Max retry attempts for 5xx/network errors',
  retry_delay_ms INT NOT NULL DEFAULT 1000 COMMENT 'Base delay between retries in ms',
  include_node_output TINYINT NOT NULL DEFAULT 0 COMMENT '1=include node output_json in ext_info',
  gmt_create TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Creation time',
  gmt_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Last modification time',
  UNIQUE KEY uk_http_callback_configs_config_id (config_id),
  KEY idx_http_callback_configs_workflow (workflow_id)
) COMMENT='HTTP callback notification configurations per workflow';