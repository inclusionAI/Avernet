-- Sparse CLI Caller overrides. Owner remains represented by no row, so this
-- table intentionally does not modify ac_bots.call_type or caller_config_revision.
CREATE TABLE ac_bot_cli_call_config (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  bot_pk BIGINT(20) UNSIGNED NOT NULL COMMENT 'ac_bots.id',
  cli_code VARCHAR(256) NOT NULL,
  engine_type VARCHAR(64) NOT NULL,
  call_type VARCHAR(16) NOT NULL COMMENT 'caller only; owner deletes the sparse row',
  modifier_id VARCHAR(1024) NOT NULL,
  revision BIGINT(20) NOT NULL DEFAULT 1 COMMENT 'scope update compensation revision',
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw' COMMENT 'data isolation tenant',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_bot_cli_call_config_scope
    (bot_pk, cli_code, engine_type, env) GLOBAL,
  KEY idx_bot_cli_call_config_scope
    (bot_pk, engine_type, env, call_type) GLOBAL
) DEFAULT CHARSET = utf8mb4 COMMENT = 'sparse Bot CLI Caller overrides';
