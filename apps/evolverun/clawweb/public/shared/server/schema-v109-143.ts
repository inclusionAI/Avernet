import type { Migration } from './schema-types.js';
import { recoveryTaskContextMigration } from "./schema-session-recovery.js";
import { cleanupEvolveSchema } from './migrations/evolve-schema-cleanup.js';
import {
  reconcilePreBaselineSkillHistory,
  reconcileProviderNeutralSkillIdentifierColumns,
  reconcileRunArchiveCreatedAt,
} from './migrations/evolve-schema-reconciliation.js';
import { repairBatchMigrations } from "./schema-repair-batch.js";
import { repairReleaseMigrations } from "./schema-repair-release.js";

/** Historical declarations; preserve statement contents and declaration order. */
export const migrationsV109To143: readonly Migration[] = [
  {
    version: 109,
    description: "Self-Evolution: bench candidate version lineage for auto-deploy gating",
    sql: [
      `CREATE TABLE IF NOT EXISTS cm_bench_candidate_versions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  lesson_id INTEGER NOT NULL COMMENT '触发的经验ID',
  candidate_version VARCHAR(64) NOT NULL COMMENT '候选版本号',
  score_vs_baseline DECIMAL(8,4) COMMENT '对基线版本的得分提升率',
  passed_baseline INTEGER NOT NULL DEFAULT 0 COMMENT '是否通过基线评测 1=是 0=否',
  overfit_detected INTEGER NOT NULL DEFAULT 0 COMMENT '是否检测到过拟合 1=是 0=否',
  deployed INTEGER NOT NULL DEFAULT 0 COMMENT '是否已发布 1=是 0=否',
  deploy_number INTEGER COMMENT '发布的deploy_number',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_cv_wf (workflow_id, gmt_create),
  INDEX idx_cv_lesson (lesson_id)
) COMMENT='候选版本谱系: 自动patch+bench+发布的每次进化记录'`,
    ],
  },
  {
    version: 110,
    description: "Add workflow evolution knowledge tables",
    sqliteOnly: true,
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_healing_lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lesson_id VARCHAR(64) NOT NULL COMMENT '经验ID',
  workflow_id VARCHAR(64) COMMENT '关联工作流ID',
  node_id VARCHAR(64) COMMENT '关联节点ID',
  failure_signature VARCHAR(512) NOT NULL COMMENT '失败签名',
  failure_mode VARCHAR(64) NOT NULL COMMENT '失败模式',
  executor_type VARCHAR(64) COMMENT '执行器类型',
  fix_kind VARCHAR(64) NOT NULL COMMENT '修法类型',
  fix_spec MEDIUMTEXT NOT NULL COMMENT '修法内容',
  status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT '状态: draft|verified|published|retired',
  confidence INTEGER NOT NULL DEFAULT 0 COMMENT '信心分0-100',
  hit_count INTEGER NOT NULL DEFAULT 0 COMMENT '命中次数',
  rescued_count INTEGER NOT NULL DEFAULT 0 COMMENT '救回次数',
  note TEXT COMMENT '备注',
  created_by VARCHAR(128) COMMENT '创建人',
  updated_by VARCHAR(128) COMMENT '更新人',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (lesson_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_healing_lessons_workflow ON workflow_healing_lessons (workflow_id, status, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_healing_lessons_signature ON workflow_healing_lessons (failure_signature, failure_mode)`,
      `CREATE TABLE IF NOT EXISTS workflow_healing_diagnoses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  diagnosis_id VARCHAR(64) NOT NULL COMMENT '诊断记录ID',
  flow_id VARCHAR(64) NOT NULL COMMENT '运行流ID',
  workflow_id VARCHAR(64) NOT NULL COMMENT '工作流ID',
  run_id VARCHAR(64) COMMENT 'Run ID',
  node_id VARCHAR(64) COMMENT '节点ID',
  failure_signature VARCHAR(512) NOT NULL COMMENT '失败签名',
  failure_mode VARCHAR(64) NOT NULL COMMENT '失败模式',
  executor_type VARCHAR(64) COMMENT '执行器类型',
  weak_node_id VARCHAR(64) COMMENT '薄弱节点ID',
  suggested_fix_kind VARCHAR(64) COMMENT '建议修法类型',
  lesson_id_hit VARCHAR(64) COMMENT '命中经验ID',
  error_text TEXT COMMENT '错误文本',
  created_by VARCHAR(128) COMMENT '创建人',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (diagnosis_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_healing_diagnoses_workflow ON workflow_healing_diagnoses (workflow_id, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_healing_diagnoses_flow ON workflow_healing_diagnoses (flow_id, gmt_create)`,
      `CREATE TABLE IF NOT EXISTS workflow_healing_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outcome_id VARCHAR(64) NOT NULL COMMENT '结果ID',
  lesson_id VARCHAR(64) NOT NULL COMMENT '经验ID',
  workflow_id VARCHAR(64) COMMENT '工作流ID',
  node_id VARCHAR(64) COMMENT '节点ID',
  action VARCHAR(64) NOT NULL COMMENT '动作',
  applied INTEGER NOT NULL DEFAULT 0 COMMENT '是否应用',
  succeeded INTEGER NOT NULL DEFAULT 0 COMMENT '是否成功',
  verdict VARCHAR(64) NOT NULL DEFAULT 'neutral' COMMENT '结果评价',
  note TEXT COMMENT '备注',
  created_by VARCHAR(128) COMMENT '创建人',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (outcome_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_healing_outcomes_lesson ON workflow_healing_outcomes (lesson_id, gmt_create)`,
    ],
  },
  {
    version: 111,
    description: "Ensure workflow_health_snapshots table exists (idempotent repair)",
    sqliteOnly: true,
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_health_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  snapshot_date VARCHAR(10) NOT NULL,
  overall_score INTEGER NOT NULL,
  success_rate DECIMAL(5,2) NOT NULL,
  node_failure_rate DECIMAL(5,4) NOT NULL,
  p95_duration_ms BIGINT,
  retry_rate DECIMAL(5,4),
  total_tokens BIGINT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE (workflow_id, snapshot_date)
)`,
      `CREATE INDEX IF NOT EXISTS idx_wh_snapshots_wf_date ON workflow_health_snapshots (workflow_id, snapshot_date)`,
    ],
  },
  {
    version: 112,
    description: "Add workflow_healing_suggestion_actions table",
    sqliteOnly: true,
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_healing_suggestion_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signature VARCHAR(512) NOT NULL,
  workflow_id VARCHAR(64) NOT NULL,
  node_id VARCHAR(64),
  action VARCHAR(32) NOT NULL,
  fix_kind VARCHAR(64),
  note TEXT,
  created_by VARCHAR(128),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (workflow_id, signature)
)`,
      `CREATE INDEX IF NOT EXISTS idx_wh_suggestion_actions_wf ON workflow_healing_suggestion_actions (workflow_id, gmt_create)`,
    ],
  },
  {
    version: 113,
    description: "Add flow_runs.evolution_analyzed_at for offline evolution analysis",
    sqliteOnly: true,
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN evolution_analyzed_at INTEGER DEFAULT NULL`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_evolution_analyzed ON flow_runs (workflow_id, evolution_analyzed_at)`,
    ],
  },
  {
    version: 114,
    description: "Add flow_runs.evolution_analysis_status for per-run analysis state",
    sqliteOnly: true,
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN evolution_analysis_status VARCHAR(16) DEFAULT NULL`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_evolution_status ON flow_runs (workflow_id, evolution_analysis_status)`,
    ],
  },
  {
    version: 115,
    description: "Add source column to workflow_healing_lessons",
    sqliteOnly: true,
    sql: [
      `ALTER TABLE workflow_healing_lessons ADD COLUMN source VARCHAR(32) DEFAULT 'log_analysis'`,
    ],
  },
  {
    version: 116,
    description: "Add workflow_healing_suggestions table for bot consumable repair specs",
    sqliteOnly: true,
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_healing_suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(64) NOT NULL COMMENT '关联工作流ID',
  node_id VARCHAR(64) COMMENT '节点ID',
  weak_node_id VARCHAR(64) COMMENT '薄弱节点ID',
  failure_signature VARCHAR(512) NOT NULL COMMENT '失败签名',
  failure_mode VARCHAR(64) COMMENT '失败模式',
  fix_kind VARCHAR(64) COMMENT '修法类型',
  fix_spec TEXT COMMENT '修法内容',
  source_diagnosis_ids TEXT COMMENT '来源诊断ID列表 JSON',
  impact_run_ids TEXT COMMENT '影响运行ID列表 JSON',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态: pending|adopted|rejected|benched|ignored|resolved',
  action_log TEXT COMMENT '操作日志 JSON 数组',
  created_by VARCHAR(128) COMMENT '创建人',
  updated_by VARCHAR(128) COMMENT '更新人',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (workflow_id, failure_signature)
)`,
      `CREATE INDEX IF NOT EXISTS idx_wh_suggestions_wf_status ON workflow_healing_suggestions (workflow_id, status, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_wh_suggestions_signature ON workflow_healing_suggestions (workflow_id, failure_signature)`,
    ],
  },
  {
    version: 117,
    description: "Support suggestion outcomes in workflow_healing_outcomes (sqlite recreate)",
    sqliteOnly: true,
    sql: [
      `CREATE TABLE workflow_healing_outcomes_v108 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outcome_id VARCHAR(64) NOT NULL,
  lesson_id VARCHAR(64),
  suggestion_id VARCHAR(64),
  workflow_id VARCHAR(64),
  node_id VARCHAR(64),
  action VARCHAR(64) NOT NULL,
  applied INTEGER NOT NULL DEFAULT 0,
  succeeded INTEGER NOT NULL DEFAULT 0,
  verdict VARCHAR(64) NOT NULL DEFAULT 'neutral',
  note TEXT,
  source_task_id VARCHAR(64),
  source_step_id VARCHAR(64),
  created_by VARCHAR(128),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (outcome_id)
)`,
      `INSERT INTO workflow_healing_outcomes_v108 (id, outcome_id, lesson_id, workflow_id, node_id, action, applied, succeeded, verdict, note, created_by, gmt_create, gmt_modified) SELECT id, outcome_id, lesson_id, workflow_id, node_id, action, applied, succeeded, verdict, note, created_by, gmt_create, gmt_modified FROM workflow_healing_outcomes`,
      `DROP TABLE workflow_healing_outcomes`,
      `ALTER TABLE workflow_healing_outcomes_v108 RENAME TO workflow_healing_outcomes`,
      `CREATE INDEX idx_workflow_healing_outcomes_lesson ON workflow_healing_outcomes (lesson_id, gmt_create)`,
      `CREATE INDEX idx_workflow_healing_outcomes_suggestion ON workflow_healing_outcomes (suggestion_id, gmt_create)`,
    ],
  },
  {
    version: 118,
    description: "Separate suggestion application from effectiveness verification",
    sqliteOnly: true,
    sql: [
      `ALTER TABLE workflow_healing_suggestions ADD COLUMN applied_at INTEGER DEFAULT NULL`,
      `ALTER TABLE workflow_healing_suggestions ADD COLUMN verification_status VARCHAR(32) NOT NULL DEFAULT 'not_started'`,
      `ALTER TABLE workflow_healing_suggestions ADD COLUMN verification_checked_at INTEGER DEFAULT NULL`,
      `ALTER TABLE workflow_healing_suggestions ADD COLUMN recurrence_count INTEGER NOT NULL DEFAULT 0`,
      `ALTER TABLE workflow_healing_suggestions ADD COLUMN last_recurrence_at INTEGER DEFAULT NULL`,
      `CREATE INDEX IF NOT EXISTS idx_wh_suggestions_verification ON workflow_healing_suggestions (verification_status, applied_at)`,
    ],
  },

  {
    version: 119,
    description: "Preserve cross-instance retry execution options",
    sql: [
      `ALTER TABLE flow_retry_requests ADD COLUMN options_json TEXT`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_origin_id ON flow_runs (origin_bot_id, id)`,
    ],
  },
  {
    version: 120,
    description: "Add owner_id to workflow_specs so save can persist the current user alongside deploy history",
    sql: [
      `ALTER TABLE workflow_specs ADD COLUMN owner_id VARCHAR(255) DEFAULT NULL`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_specs_owner_id ON workflow_specs (owner_id)`,
    ],
  },
  {
    version: 121,
    description: "ClawWeb: run_archive_records table for persisted run archives with AI analysis",
    migrate: reconcileRunArchiveCreatedAt,
    sql: [
      `CREATE TABLE IF NOT EXISTS run_archive_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  workflow_version INTEGER DEFAULT NULL,
  workflow_deploy_number INTEGER DEFAULT NULL,
  archive_version VARCHAR(32) NOT NULL DEFAULT '3.1.0',
  status VARCHAR(32) NOT NULL DEFAULT 'completed',
  run_status VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL,
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
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_run_archive_aid ON run_archive_records (archive_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_archive_flow ON run_archive_records (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_archive_wf ON run_archive_records (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_archive_created ON run_archive_records (created_at)`,
    ],
  },
  {
    version: 122,
    description: "Add Stage Skill extensions, HITL audit, and registered Skill version assets",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_stage_skill_implementations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_skill_id VARCHAR(64) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  version_no INTEGER NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'validated',
  package_ref TEXT NOT NULL,
  package_sha256 VARCHAR(64) NOT NULL,
  static_validation_json TEXT NOT NULL,
  integration_test_task_id VARCHAR(64),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (implementation_id),
  UNIQUE (stage_skill_id, version_no)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_skill_owner ON ce_stage_skill_implementations (owner_user_id, stage_key, extension_mode, stage_skill_id, gmt_create)`,
      `CREATE TABLE IF NOT EXISTS ce_stage_extension_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  initial_input_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (step_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_extension_task ON ce_stage_extension_runs (task_id, stage_key, id)`,
      `CREATE TABLE IF NOT EXISTS ce_stage_interactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  interaction_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(64) NOT NULL,
  attempt_no INTEGER NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'waiting',
  request_json TEXT NOT NULL,
  response_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (interaction_id),
  UNIQUE (step_id, attempt_no)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_interaction_task ON ce_stage_interactions (task_id, step_id, status)`,
      `CREATE TABLE IF NOT EXISTS ce_skill_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  current_version_no INTEGER NOT NULL DEFAULT 1,
  current_package_ref TEXT NOT NULL,
  current_package_sha256 VARCHAR(80) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (asset_id),
  UNIQUE (owner_user_id, bot_id, ocb_skill_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_asset_owner ON ce_skill_assets (owner_user_id, bot_id, gmt_create)`,
      `CREATE TABLE IF NOT EXISTS ce_skill_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version_id VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  version_no INTEGER NOT NULL,
  package_ref TEXT NOT NULL,
  package_sha256 VARCHAR(80) NOT NULL,
  source_task_id VARCHAR(64),
  baseline_package_ref TEXT,
  baseline_package_sha256 VARCHAR(80),
  status VARCHAR(32) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (version_id),
  UNIQUE (asset_id, version_no),
  UNIQUE (asset_id, source_task_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_version_asset ON ce_skill_versions (asset_id, version_no)`,
    ],
  },
  {
    version: 123,
    description: "Keep Stage Skill registration and integration-test verification as separate states",
    sql: [
      `ALTER TABLE ce_stage_skill_implementations ADD COLUMN integration_test_status VARCHAR(32) NOT NULL DEFAULT 'untested'`,
    ],
  },
  {
    version: 124,
    description: "Persist custom Stage development before package upload",
    sql: [
      `ALTER TABLE ce_skill_assets ADD COLUMN description TEXT`,
      `CREATE TABLE IF NOT EXISTS ce_stage_developments (
  stage_skill_id VARCHAR(64) PRIMARY KEY,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  flow_key VARCHAR(32) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  gmt_create INTEGER NOT NULL,
  gmt_modified INTEGER NOT NULL
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_development_owner ON ce_stage_developments (owner_user_id, gmt_modified)`,
    ],
  },
  {
    version: 125,
    description: "Persist operation-time Skill audit records without historical backfill",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_skill_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  description TEXT,
  task_id VARCHAR(64),
  version_id VARCHAR(64),
  version_no INTEGER,
  event_type VARCHAR(32) NOT NULL,
  actor_id VARCHAR(128),
  actor_type VARCHAR(16) NOT NULL,
  result VARCHAR(64) NOT NULL,
  detail_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (event_id),
  UNIQUE (idempotency_key)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_audit_owner ON ce_skill_audit_events (owner_user_id, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_audit_task ON ce_skill_audit_events (task_id, event_type)`,
    ],
  },
  {
    version: 126,
    description: "Reconcile workflow ownership for pre-merge Stage Skill databases",
    sql: [
      // Existing feature databases recorded versions 120–123 before upstream
      // version 120 added ownership. Reapply its idempotent DDL above that range.
      `ALTER TABLE workflow_specs ADD COLUMN owner_id VARCHAR(255) DEFAULT NULL`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_specs_owner_id ON workflow_specs (owner_id)`,
    ],
  },
  {
    version: 127,
    description: "Bind Evolve assets to host spaces without sharing historical private records",
    sql: [
      ...["ce_skill_assets", "ce_stage_developments", "ce_stage_skill_implementations"].flatMap((table) => [
        `ALTER TABLE ${table} ADD COLUMN space_id VARCHAR(128) DEFAULT NULL`,
        `ALTER TABLE ${table} ADD COLUMN space_type VARCHAR(16) DEFAULT NULL`,
        `ALTER TABLE ${table} ADD COLUMN space_name VARCHAR(255) DEFAULT NULL`,
        `CREATE INDEX IF NOT EXISTS idx_${table}_space ON ${table} (space_id)`,
      ]),
    ],
  },
  {
    version: 128,
    description: "Normalize Skill history into one business event per task",
    migrate: reconcilePreBaselineSkillHistory,
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_skill_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id VARCHAR(64) NOT NULL,
  business_key VARCHAR(255) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  description TEXT,
  task_id VARCHAR(64),
  event_type VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  outcome VARCHAR(64),
  actor_id VARCHAR(128),
  actor_type VARCHAR(16) NOT NULL,
  version_from_id VARCHAR(64),
  version_from_no INTEGER,
  version_to_id VARCHAR(64),
  version_to_no INTEGER,
  waiting_interaction_id VARCHAR(64),
  summary TEXT,
  detail_json TEXT,
  started_at INTEGER NOT NULL,
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (event_id),
  UNIQUE (business_key),
  UNIQUE (task_id),
  INDEX idx_ce_skill_event_owner (owner_user_id, gmt_create),
  INDEX idx_ce_skill_event_asset (asset_id, gmt_create),
  INDEX idx_ce_skill_event_task (task_id)
)`,
      `INSERT INTO ce_skill_events
       (event_id, business_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
        task_id, event_type, status, outcome, actor_id, actor_type, version_from_id, version_from_no,
        started_at, completed_at, gmt_create, gmt_modified)
       SELECT event_id, idempotency_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
        NULL, 'registered', 'completed', result, actor_id, actor_type, version_id, version_no,
        gmt_create, gmt_create, gmt_create, gmt_create
       FROM ce_skill_audit_events source WHERE event_type = 'registered'
         AND NOT EXISTS (SELECT 1 FROM ce_skill_events target WHERE target.event_id = source.event_id)`,
      `INSERT INTO ce_skill_events
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
         AND NOT EXISTS (SELECT 1 FROM ce_skill_events target WHERE target.task_id = first_event.task_id)`,
    ],
  },
  {
    version: 129,
    description: "Store optional Stage feedback-loop lineage on Evolve steps",
    sql: [
      `ALTER TABLE ce_steps ADD COLUMN ext_json TEXT`,
    ],
  },
  {
    version: 130,
    description: "Track manually-created Skill version origin and creation kind",
    sql: [
      `ALTER TABLE ce_skill_versions ADD COLUMN creation_kind VARCHAR(16) NOT NULL DEFAULT 'task'`,
      `ALTER TABLE ce_skill_versions ADD COLUMN source_version_id VARCHAR(64)`,
      `ALTER TABLE ce_skill_versions ADD COLUMN source_version_no INTEGER`,
      `ALTER TABLE ce_skill_versions ADD COLUMN created_by VARCHAR(128)`,
      `UPDATE ce_skill_versions SET creation_kind = 'registered' WHERE status = 'baseline'`,
    ],
  },
  {
    version: 131,
    description: "Reconcile run archives for pre-rebase Evolve feature databases",
    migrate: reconcileRunArchiveCreatedAt,
    sql: [
      `CREATE TABLE IF NOT EXISTS run_archive_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  workflow_version INTEGER DEFAULT NULL,
  workflow_deploy_number INTEGER DEFAULT NULL,
  archive_version VARCHAR(32) NOT NULL DEFAULT '3.1.0',
  status VARCHAR(32) NOT NULL DEFAULT 'completed',
  run_status VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL,
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
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_run_archive_aid ON run_archive_records (archive_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_archive_flow ON run_archive_records (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_archive_wf ON run_archive_records (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_archive_created ON run_archive_records (created_at)`,
    ],
  },
  {
    version: 132,
    description: "Use provider-neutral Skill identifiers without changing stored values",
    migrate: reconcileProviderNeutralSkillIdentifierColumns,
    sql: [],
  },
  {
    version: 133,
    description: "Persist in-flight Skill application for retry after external success",
    sql: [`ALTER TABLE ce_skill_assets ADD COLUMN pending_application_json TEXT`],
  },
  {
    version: 134,
    description: "Add generic JSON Evolve application configuration",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_app_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_key VARCHAR(64) NOT NULL,
  config_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  updated_by VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_ce_app_config_key ON ce_app_config (config_key)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_app_config_enabled ON ce_app_config (enabled)`,
    ],
  },
  {
    version: 135,
    description: "Remove redundant Evolve fields and indexes, preserve feature data",
    sql: [],
    migrate: cleanupEvolveSchema,
  },
  ...repairBatchMigrations,
  {
    version: 139,
    description: "Preserve the selected Bot environment for Skill assets",
    sql: [`ALTER TABLE ce_skill_assets ADD COLUMN bot_env VARCHAR(32) DEFAULT NULL COMMENT '来源Bot环境；历史未记录时为空'`],
  },
  {
    version: 140,
    description: "Fence workflow completion and timeout writes by manual retry epoch",
    sql: [`ALTER TABLE flow_runs ADD COLUMN retry_epoch INTEGER DEFAULT 0`],
  },
  recoveryTaskContextMigration,
  ...repairReleaseMigrations,
  {
    version: 143,
    description: "Bind Bench Domains to host spaces while preserving personal history",
    sql: [`ALTER TABLE cm_bench_domains ADD COLUMN space_id VARCHAR(128) DEFAULT NULL COMMENT '所属空间ID；NULL保留原Owner个人归属'`],
  },
];
