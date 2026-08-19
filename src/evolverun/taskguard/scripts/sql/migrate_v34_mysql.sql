-- Migration v34: Create run_logs table for console log capture (MySQL/OceanBase)
-- Stores console.log/warn/error output captured during workflow execution,
-- keyed by flow_id for run archive generation.

CREATE TABLE IF NOT EXISTS run_logs (
  id BIGINT NOT NULL AUTO_INCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  level VARCHAR(32) NOT NULL,
  source VARCHAR(255),
  message TEXT NOT NULL,
  timestamp BIGINT NOT NULL,
  seq INTEGER NOT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  INDEX idx_run_logs_flow_id (flow_id),
  INDEX idx_run_logs_flow_node (flow_id, node_id),
  INDEX idx_run_logs_level (flow_id, level)
) COMMENT = 'Console log capture table for run archive';
