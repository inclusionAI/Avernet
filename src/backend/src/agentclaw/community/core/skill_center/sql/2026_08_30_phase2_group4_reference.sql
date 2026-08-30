-- Phase 2 Group 4: durable SC Public Reference batch/item operations.

CREATE INDEX IF NOT EXISTS idx_skill_center_public_locator
  ON ac_skill (avernet_tenant, env, git_path);

CREATE TABLE IF NOT EXISTS ac_skill_center_reference_batch (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  request_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(190) NOT NULL,
  request_hash VARCHAR(64) NOT NULL,
  bot_id VARCHAR(100) NOT NULL,
  owner_id VARCHAR(128) NOT NULL,
  skill_set_id VARCHAR(64) NOT NULL,
  actor_id VARCHAR(128) NOT NULL,
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sc_reference_request (avernet_tenant, env, request_id),
  UNIQUE KEY uk_sc_reference_idempotency (avernet_tenant, env, idempotency_key),
  CONSTRAINT ck_sc_reference_batch_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_skill_center_reference_item (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  reference_id VARCHAR(64) NOT NULL,
  request_id VARCHAR(64) NOT NULL,
  bot_id VARCHAR(100) NOT NULL,
  owner_id VARCHAR(128) NOT NULL,
  skill_set_id VARCHAR(64) NOT NULL,
  actor_id VARCHAR(128) NOT NULL,
  skill_code VARCHAR(512) NOT NULL,
  sc_version_number VARCHAR(128) NULL,
  skill_version_id BIGINT UNSIGNED NULL,
  resolved_skill_id BIGINT UNSIGNED NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'QUEUED',
  attempt_count INT NOT NULL DEFAULT 0,
  error_code VARCHAR(128) NULL,
  error_message TEXT NULL,
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sc_reference_id (avernet_tenant, env, reference_id),
  UNIQUE KEY uk_sc_reference_code (avernet_tenant, env, request_id, skill_code),
  KEY idx_sc_reference_collection
    (avernet_tenant, env, bot_id, owner_id, skill_set_id, gmt_created, id),
  KEY idx_sc_reference_request_items (avernet_tenant, env, request_id, id),
  CONSTRAINT ck_sc_reference_status CHECK (status IN
    ('QUEUED', 'RESOLVING_VERSION', 'MATERIALIZING',
     'ADDING_TO_SKILL_SET', 'PROJECTING_RUNTIME', 'COMPLETED', 'FAILED')),
  CONSTRAINT ck_sc_reference_attempt_count CHECK (attempt_count >= 0),
  CONSTRAINT ck_sc_reference_item_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
