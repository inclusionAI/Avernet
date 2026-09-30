-- Migration v134: generic Evolve JSON application configuration.
-- Run before deploying the DB-backed Skill task Stage binding reader.
-- No provider IDs or configuration values are seeded by this DDL.
CREATE TABLE IF NOT EXISTS ce_app_config (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  config_key VARCHAR(64) NOT NULL,
  config_json TEXT NOT NULL,
  version BIGINT NOT NULL DEFAULT 1,
  enabled BIGINT NOT NULL DEFAULT 1,
  description TEXT,
  updated_by VARCHAR(190),
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ce_app_config_key (config_key),
  KEY idx_ce_app_config_enabled (enabled)
);
