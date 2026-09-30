-- ClawWeb Evolve release; migrations 122-133; canonical source: public/shared/server/schema.ts.
-- For FIRST deployment: these eight tables must not already exist.
-- Later ADD COLUMN migrations are folded into CREATE TABLE.
-- MySQL types rendered by the repository dialect; indexes placed inline.
-- Does not change schema_version or existing data.
-- Not executed against the target MySQL / ZDAS database.

CREATE TABLE IF NOT EXISTS ce_stage_skill_implementations (
  space_name VARCHAR(190) DEFAULT NULL,
  space_type VARCHAR(16) DEFAULT NULL,
  space_id VARCHAR(128) DEFAULT NULL,
  integration_test_status VARCHAR(32) NOT NULL DEFAULT 'untested',
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  stage_skill_id VARCHAR(64) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  version_no BIGINT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'validated',
  package_ref TEXT NOT NULL,
  package_sha256 VARCHAR(64) NOT NULL,
  static_validation_json TEXT NOT NULL,
  integration_test_task_id VARCHAR(64),
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE (implementation_id),
  UNIQUE (stage_skill_id, version_no),
  INDEX idx_ce_stage_skill_owner (owner_user_id, stage_key, extension_mode, stage_skill_id, gmt_create),
  INDEX idx_ce_stage_skill_implementations_space (space_id)
);

CREATE TABLE IF NOT EXISTS ce_stage_extension_runs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  step_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  initial_input_json TEXT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (step_id),
  INDEX idx_ce_stage_extension_task (task_id, stage_key, id)
);

CREATE TABLE IF NOT EXISTS ce_stage_interactions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  interaction_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(64) NOT NULL,
  attempt_no BIGINT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'waiting',
  request_json TEXT NOT NULL,
  response_json TEXT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE (interaction_id),
  UNIQUE (step_id, attempt_no),
  INDEX idx_ce_stage_interaction_task (task_id, step_id, status)
);

CREATE TABLE IF NOT EXISTS ce_skill_assets (
  pending_application_json TEXT,
  space_name VARCHAR(190) DEFAULT NULL,
  space_type VARCHAR(16) DEFAULT NULL,
  space_id VARCHAR(128) DEFAULT NULL,
  description TEXT,
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  external_skill_id VARCHAR(190) NOT NULL,
  display_name VARCHAR(190) NOT NULL,
  current_version_no BIGINT NOT NULL DEFAULT 1,
  current_package_ref TEXT NOT NULL,
  current_package_sha256 VARCHAR(80) NOT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE (asset_id),
  UNIQUE (owner_user_id, bot_id, external_skill_id),
  INDEX idx_ce_skill_asset_owner (owner_user_id, bot_id, gmt_create),
  INDEX idx_ce_skill_assets_space (space_id)
);

CREATE TABLE IF NOT EXISTS ce_skill_versions (
  created_by VARCHAR(128),
  source_version_no BIGINT,
  source_version_id VARCHAR(64),
  creation_kind VARCHAR(16) NOT NULL DEFAULT 'task',
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  version_id VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  version_no BIGINT NOT NULL,
  package_ref TEXT NOT NULL,
  package_sha256 VARCHAR(80) NOT NULL,
  source_task_id VARCHAR(64),
  baseline_package_ref TEXT,
  baseline_package_sha256 VARCHAR(80),
  status VARCHAR(32) NOT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (version_id),
  UNIQUE (asset_id, version_no),
  UNIQUE (asset_id, source_task_id),
  INDEX idx_ce_skill_version_asset (asset_id, version_no)
);

CREATE TABLE IF NOT EXISTS ce_stage_developments (
  space_name VARCHAR(190) DEFAULT NULL,
  space_type VARCHAR(16) DEFAULT NULL,
  space_id VARCHAR(128) DEFAULT NULL,
  stage_skill_id VARCHAR(64) PRIMARY KEY,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  flow_key VARCHAR(32) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  gmt_create BIGINT NOT NULL,
  gmt_modified BIGINT NOT NULL,
  INDEX idx_ce_stage_development_owner (owner_user_id, gmt_modified),
  INDEX idx_ce_stage_developments_space (space_id)
);

CREATE TABLE IF NOT EXISTS ce_skill_audit_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  external_skill_id VARCHAR(190) NOT NULL,
  display_name VARCHAR(190) NOT NULL,
  description TEXT,
  task_id VARCHAR(64),
  version_id VARCHAR(64),
  version_no BIGINT,
  event_type VARCHAR(32) NOT NULL,
  actor_id VARCHAR(128),
  actor_type VARCHAR(16) NOT NULL,
  result VARCHAR(64) NOT NULL,
  detail_json TEXT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (event_id),
  UNIQUE (idempotency_key),
  INDEX idx_ce_skill_audit_owner (owner_user_id, gmt_create),
  INDEX idx_ce_skill_audit_task (task_id, event_type)
);

CREATE TABLE IF NOT EXISTS ce_skill_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(64) NOT NULL,
  business_key VARCHAR(190) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  external_skill_id VARCHAR(190) NOT NULL,
  display_name VARCHAR(190) NOT NULL,
  description TEXT,
  task_id VARCHAR(64),
  event_type VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  outcome VARCHAR(64),
  actor_id VARCHAR(128),
  actor_type VARCHAR(16) NOT NULL,
  version_from_id VARCHAR(64),
  version_from_no BIGINT,
  version_to_id VARCHAR(64),
  version_to_no BIGINT,
  waiting_interaction_id VARCHAR(64),
  summary TEXT,
  detail_json TEXT,
  started_at BIGINT NOT NULL,
  completed_at BIGINT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE (event_id),
  UNIQUE (business_key),
  UNIQUE (task_id),
  INDEX idx_ce_skill_event_owner (owner_user_id, gmt_create),
  INDEX idx_ce_skill_event_asset (asset_id, gmt_create),
  INDEX idx_ce_skill_event_task (task_id)
);
