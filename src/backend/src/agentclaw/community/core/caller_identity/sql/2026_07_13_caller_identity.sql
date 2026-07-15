-- Service-Bot Caller identity: aggregate mode plus sparse MCP overrides.
-- Existing Bots default to owner, so legacy chat paths require no migration.

ALTER TABLE ac_bots
  ADD COLUMN call_type VARCHAR(16) NOT NULL DEFAULT 'owner'
    COMMENT 'aggregate MCP call type: owner/caller',
  ADD COLUMN caller_config_revision BIGINT(20) NOT NULL DEFAULT 0
    COMMENT 'revision used only to compensate a failed Agent Principal sync';

CREATE TABLE ac_bot_mcp_call_config (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  bot_pk BIGINT(20) UNSIGNED NOT NULL,
  server_code VARCHAR(256) NOT NULL,
  engine_type VARCHAR(64) NOT NULL,
  call_type VARCHAR(16) NOT NULL,
  modifier_id VARCHAR(1024) NOT NULL,
  env VARCHAR(20) NOT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_bot_mcp_call_config_scope
    (bot_pk, server_code, engine_type, env) GLOBAL,
  KEY idx_bot_mcp_call_config_aggregate
    (bot_pk, engine_type, env, call_type) GLOBAL
) DEFAULT CHARSET = utf8mb4 COMMENT = 'sparse Bot MCP Caller overrides';
