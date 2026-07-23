-- Bot 级 Skills Layout 控制面状态。
-- 缺少记录等价于 LEGACY_ACTIVE，因此升级前数据无需回填。

CREATE TABLE ac_bot_skill_layout_state (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  env VARCHAR(20) NOT NULL,
  entity_id VARCHAR(512) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  active_layout VARCHAR(20) NOT NULL DEFAULT 'legacy',
  target_layout VARCHAR(20) NULL,
  phase VARCHAR(64) NOT NULL DEFAULT 'legacy_active',
  migration_generation VARCHAR(64) NULL,
  layout_contract_version VARCHAR(64) NULL,
  preparation_id VARCHAR(64) NULL,
  last_probe_result VARCHAR(32) NULL,
  last_probe_evidence TEXT NULL,
  data_plane_cutover_committed SMALLINT NOT NULL DEFAULT 0,
  last_failure_code VARCHAR(64) NULL,
  last_failure_stage VARCHAR(64) NULL,
  last_failure_retryable SMALLINT NULL,
  last_failure_at DATETIME NULL,
  pool_activated_at DATETIME NULL,
  lease_owner VARCHAR(128) NULL,
  lease_expires_at DATETIME NULL,
  rollout_evidence TEXT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_bot_skill_layout_state_scope
    (env, entity_id, bot_id) GLOBAL,
  KEY idx_bot_skill_layout_state_lease
    (env, phase, lease_expires_at) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot Skills Layout durable migration state';
