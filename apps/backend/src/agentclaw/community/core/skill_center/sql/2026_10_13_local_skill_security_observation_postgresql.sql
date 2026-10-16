-- One row per Local Skill upload; no ac_skill schema change.
-- Apply before deploying the matching ORM code when create_schema=false.
CREATE TABLE IF NOT EXISTS ac_local_skill_security_observation (
  id BIGSERIAL PRIMARY KEY,
  observation_id VARCHAR(128) NOT NULL UNIQUE,
  upload_order BIGINT NULL,
  provider_skill_id VARCHAR(128) NOT NULL,
  skill_id BIGINT NULL,
  incarnation_id VARCHAR(128) NOT NULL,
  owner_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(100) NOT NULL,
  creator_id VARCHAR(128) NULL,
  modifier_id VARCHAR(128) NOT NULL,
  name VARCHAR(255) NULL,
  description TEXT NULL,
  sha256 VARCHAR(71) NOT NULL,
  snapshot_ref VARCHAR(1028) NOT NULL,
  handoff_stage VARCHAR(32) NOT NULL DEFAULT 'PENDING',
  observation_committed INTEGER NOT NULL DEFAULT 0,
  snapshot_verified INTEGER NOT NULL DEFAULT 0,
  repair_claimed INTEGER NOT NULL DEFAULT 0,
  external_artifact_json TEXT NULL,
  check_id VARCHAR(128) NULL,
  processing_status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
  risk_status VARCHAR(24) NULL,
  report_json TEXT NULL,
  detail_url VARCHAR(2048) NULL,
  scan_started_at TIMESTAMP NULL,
  scan_completed_at TIMESTAMP NULL,
  error_code VARCHAR(128) NULL,
  error_message TEXT NULL,
  sync_status VARCHAR(24) NOT NULL DEFAULT 'NOT_READY',
  sync_error_code VARCHAR(128) NULL,
  sync_error_message TEXT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_local_security_check UNIQUE (avernet_tenant, env, check_id)
);
CREATE INDEX IF NOT EXISTS idx_local_security_history
  ON ac_local_skill_security_observation (avernet_tenant, env, owner_id, bot_id, skill_id, upload_order);
CREATE UNIQUE INDEX IF NOT EXISTS uk_local_security_upload_order
  ON ac_local_skill_security_observation (avernet_tenant, env, bot_id, owner_id, skill_id, upload_order);
CREATE INDEX IF NOT EXISTS idx_local_security_provider
  ON ac_local_skill_security_observation (avernet_tenant, env, provider_skill_id, upload_order);
CREATE INDEX IF NOT EXISTS idx_local_security_recover_scan
  ON ac_local_skill_security_observation (env, processing_status, gmt_modified, avernet_tenant);
CREATE INDEX IF NOT EXISTS idx_local_security_recover_sync
  ON ac_local_skill_security_observation (env, sync_status, gmt_modified, avernet_tenant);
CREATE INDEX IF NOT EXISTS idx_local_security_recover_handoff
  ON ac_local_skill_security_observation (env, handoff_stage, gmt_created, avernet_tenant);
