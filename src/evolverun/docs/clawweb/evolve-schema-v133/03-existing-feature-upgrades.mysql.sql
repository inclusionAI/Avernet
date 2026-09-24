-- Incremental reference for existing feature databases. Do NOT execute this whole file.
-- Select only missing statements after 00-preflight; v128 data copy must precede v132 renames.
-- Rendered from schema.ts using mysqlDialect. Runtime DDL is not required by managed hosts.

-- v122: Add Stage Skill extensions, HITL audit, and registered Skill version assets
CREATE TABLE IF NOT EXISTS ce_stage_skill_implementations (
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
  UNIQUE (stage_skill_id, version_no)
);
CREATE INDEX idx_ce_stage_skill_owner ON ce_stage_skill_implementations (owner_user_id, stage_key, extension_mode, stage_skill_id, gmt_create);
CREATE TABLE IF NOT EXISTS ce_stage_extension_runs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  step_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  initial_input_json TEXT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (step_id)
);
CREATE INDEX idx_ce_stage_extension_task ON ce_stage_extension_runs (task_id, stage_key, id);
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
  UNIQUE (step_id, attempt_no)
);
CREATE INDEX idx_ce_stage_interaction_task ON ce_stage_interactions (task_id, step_id, status);
CREATE TABLE IF NOT EXISTS ce_skill_assets (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(190) NOT NULL,
  display_name VARCHAR(190) NOT NULL,
  current_version_no BIGINT NOT NULL DEFAULT 1,
  current_package_ref TEXT NOT NULL,
  current_package_sha256 VARCHAR(80) NOT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE (asset_id),
  UNIQUE (owner_user_id, bot_id, ocb_skill_id)
);
CREATE INDEX idx_ce_skill_asset_owner ON ce_skill_assets (owner_user_id, bot_id, gmt_create);
CREATE TABLE IF NOT EXISTS ce_skill_versions (
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
  UNIQUE (asset_id, source_task_id)
);
CREATE INDEX idx_ce_skill_version_asset ON ce_skill_versions (asset_id, version_no);

-- v123: Keep Stage Skill registration and integration-test verification as separate states
ALTER TABLE ce_stage_skill_implementations ADD COLUMN integration_test_status VARCHAR(32) NOT NULL DEFAULT 'untested';

-- v124: Persist custom Stage development before package upload
ALTER TABLE ce_skill_assets ADD COLUMN description TEXT;
CREATE TABLE IF NOT EXISTS ce_stage_developments (
  stage_skill_id VARCHAR(64) PRIMARY KEY,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  flow_key VARCHAR(32) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  gmt_create BIGINT NOT NULL,
  gmt_modified BIGINT NOT NULL
);
CREATE INDEX idx_ce_stage_development_owner ON ce_stage_developments (owner_user_id, gmt_modified);

-- v125: Persist operation-time Skill audit records without historical backfill
CREATE TABLE IF NOT EXISTS ce_skill_audit_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(190) NOT NULL,
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
  UNIQUE (idempotency_key)
);
CREATE INDEX idx_ce_skill_audit_owner ON ce_skill_audit_events (owner_user_id, gmt_create);
CREATE INDEX idx_ce_skill_audit_task ON ce_skill_audit_events (task_id, event_type);

-- v126: Reconcile workflow ownership for pre-merge Stage Skill databases
ALTER TABLE workflow_specs ADD COLUMN owner_id VARCHAR(190) DEFAULT NULL;
CREATE INDEX idx_workflow_specs_owner_id ON workflow_specs (owner_id);

-- v127: Bind Evolve assets to host spaces without sharing historical private records
ALTER TABLE ce_skill_assets ADD COLUMN space_id VARCHAR(128) DEFAULT NULL;
ALTER TABLE ce_skill_assets ADD COLUMN space_type VARCHAR(16) DEFAULT NULL;
ALTER TABLE ce_skill_assets ADD COLUMN space_name VARCHAR(190) DEFAULT NULL;
CREATE INDEX idx_ce_skill_assets_space ON ce_skill_assets (space_id);
ALTER TABLE ce_stage_developments ADD COLUMN space_id VARCHAR(128) DEFAULT NULL;
ALTER TABLE ce_stage_developments ADD COLUMN space_type VARCHAR(16) DEFAULT NULL;
ALTER TABLE ce_stage_developments ADD COLUMN space_name VARCHAR(190) DEFAULT NULL;
CREATE INDEX idx_ce_stage_developments_space ON ce_stage_developments (space_id);
ALTER TABLE ce_stage_skill_implementations ADD COLUMN space_id VARCHAR(128) DEFAULT NULL;
ALTER TABLE ce_stage_skill_implementations ADD COLUMN space_type VARCHAR(16) DEFAULT NULL;
ALTER TABLE ce_stage_skill_implementations ADD COLUMN space_name VARCHAR(190) DEFAULT NULL;
CREATE INDEX idx_ce_stage_skill_implementations_space ON ce_stage_skill_implementations (space_id);

-- v128: Normalize Skill history into one business event per task
CREATE TABLE IF NOT EXISTS ce_skill_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(64) NOT NULL,
  business_key VARCHAR(190) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(190) NOT NULL,
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
INSERT INTO ce_skill_events
       (event_id, business_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
        task_id, event_type, status, outcome, actor_id, actor_type, version_from_id, version_from_no,
        started_at, completed_at, gmt_create, gmt_modified)
       SELECT event_id, idempotency_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
        NULL, 'registered', 'completed', result, actor_id, actor_type, version_id, version_no,
        gmt_create, gmt_create, gmt_create, gmt_create
       FROM ce_skill_audit_events source WHERE event_type = 'registered'
         AND NOT EXISTS (SELECT 1 FROM ce_skill_events target WHERE target.event_id = source.event_id);
INSERT INTO ce_skill_events
       (event_id, business_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
        task_id, event_type, status, outcome, actor_id, actor_type, version_from_id, version_from_no,
        version_to_id, version_to_no, waiting_interaction_id, summary, detail_json,
        started_at, completed_at, gmt_create, gmt_modified)
       SELECT first_event.event_id, first_event.task_id, first_event.asset_id, first_event.owner_user_id,
        first_event.bot_id, first_event.ocb_skill_id, first_event.display_name, first_event.description,
        first_event.task_id,
        CASE WHEN task.task_type = 'diagnose' THEN 'diagnosis' ELSE 'optimization' END,
        CASE
          WHEN task.status IN ('completed', 'failed', 'canceled') THEN task.status
          WHEN task.status = 'waiting_acceptance' THEN 'waiting_acceptance'
          WHEN EXISTS (SELECT 1 FROM ce_stage_interactions interaction
            JOIN ce_steps interaction_step ON interaction_step.step_id = interaction.step_id
            WHERE interaction.task_id = first_event.task_id AND interaction.status = 'waiting'
              AND interaction_step.status = 'waiting_context') THEN 'waiting_user_input'
          ELSE 'running'
        END,
        (SELECT terminal.result FROM ce_skill_audit_events terminal
          WHERE terminal.task_id = first_event.task_id
            AND terminal.event_type IN ('diagnosis_finished', 'evolution_finished', 'candidate_rejected',
              'version_applied', 'version_apply_failed')
          ORDER BY terminal.id DESC LIMIT 1),
        first_event.actor_id, first_event.actor_type, first_event.version_id, first_event.version_no,
        target_version.version_id, target_version.version_no,
        (SELECT interaction.interaction_id FROM ce_stage_interactions interaction
          WHERE interaction.task_id = first_event.task_id AND interaction.status = 'waiting'
          ORDER BY interaction.id DESC LIMIT 1),
        task.task_name,
        (SELECT terminal.detail_json FROM ce_skill_audit_events terminal
          WHERE terminal.task_id = first_event.task_id AND terminal.detail_json IS NOT NULL
          ORDER BY terminal.id DESC LIMIT 1),
        first_event.gmt_create,
        CASE WHEN task.status IN ('completed', 'failed', 'canceled') THEN task.gmt_modified ELSE NULL END,
        first_event.gmt_create, task.gmt_modified
       FROM ce_skill_audit_events first_event
       JOIN (
         SELECT task_id, MIN(id) AS first_id FROM ce_skill_audit_events
         WHERE task_id IS NOT NULL AND event_type IN ('diagnosis_started', 'evolution_started')
         GROUP BY task_id
       ) first_by_task ON first_by_task.first_id = first_event.id
       JOIN ce_tasks task ON task.task_id = first_event.task_id
       LEFT JOIN ce_skill_versions target_version
         ON target_version.asset_id = first_event.asset_id AND target_version.source_task_id = first_event.task_id
       WHERE task.task_type IN ('diagnose', 'full')
         AND NOT EXISTS (SELECT 1 FROM ce_skill_events target WHERE target.task_id = first_event.task_id);

-- v129: Store optional Stage feedback-loop lineage on Evolve steps
ALTER TABLE ce_steps ADD COLUMN ext_json TEXT;

-- v130: Track manually-created Skill version origin and creation kind
ALTER TABLE ce_skill_versions ADD COLUMN creation_kind VARCHAR(16) NOT NULL DEFAULT 'task';
ALTER TABLE ce_skill_versions ADD COLUMN source_version_id VARCHAR(64);
ALTER TABLE ce_skill_versions ADD COLUMN source_version_no BIGINT;
ALTER TABLE ce_skill_versions ADD COLUMN created_by VARCHAR(128);
UPDATE ce_skill_versions SET creation_kind = 'registered' WHERE status = 'baseline';

-- v131: Reconcile run archives for pre-rebase Evolve feature databases
CREATE TABLE IF NOT EXISTS run_archive_records (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  archive_id VARCHAR(190) NOT NULL,
  flow_id VARCHAR(190) NOT NULL,
  workflow_id VARCHAR(190) NOT NULL,
  workflow_version BIGINT DEFAULT NULL,
  workflow_deploy_number BIGINT DEFAULT NULL,
  archive_version VARCHAR(32) NOT NULL DEFAULT '3.1.0',
  status VARCHAR(32) NOT NULL DEFAULT 'completed',
  run_status VARCHAR(32) NOT NULL,
  created_at TEXT NOT NULL,
  run_process_json MEDIUMTEXT,
  workflow_spec_json MEDIUMTEXT,
  workflow_deploy_history_json MEDIUMTEXT,
  error_details_json MEDIUMTEXT,
  analysis_json MEDIUMTEXT,
  analysis_source VARCHAR(32) DEFAULT 'pending',
  analysis_status VARCHAR(32) DEFAULT 'pending',
  analysis_error TEXT,
  suggested_yaml TEXT,
  patch_proposal_json TEXT,
  yaml_summary TEXT,
  yaml_confidence VARCHAR(16) DEFAULT 'low',
  errors_json TEXT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uk_run_archive_aid ON run_archive_records (archive_id);
CREATE INDEX idx_run_archive_flow ON run_archive_records (flow_id);
CREATE INDEX idx_run_archive_wf ON run_archive_records (workflow_id);
CREATE INDEX idx_run_archive_created ON run_archive_records (created_at(191));

-- v132: Use provider-neutral Skill identifiers without changing stored values
ALTER TABLE ce_skill_assets RENAME COLUMN ocb_skill_id TO external_skill_id;
ALTER TABLE ce_skill_audit_events RENAME COLUMN ocb_skill_id TO external_skill_id;
ALTER TABLE ce_skill_events RENAME COLUMN ocb_skill_id TO external_skill_id;

-- v133: Persist in-flight Skill application for retry after external success
ALTER TABLE ce_skill_assets ADD COLUMN pending_application_json TEXT;
