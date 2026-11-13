import type { Migration } from './schema-types.js';

/** Historical declarations; preserve statement contents and declaration order. */
export const migrationsV75To108: readonly Migration[] = [
  {
    version: 75,
    description: "Add Insight Center improvement item, evidence snapshot, and Evolve link tables",
    sql: [
      `CREATE TABLE IF NOT EXISTS insight_improvement_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id VARCHAR(128) NOT NULL DEFAULT '',
  bot_id VARCHAR(128) NOT NULL DEFAULT '',
  title VARCHAR(256) NOT NULL DEFAULT '',
  user_guidance TEXT,
  source_type VARCHAR(32) NOT NULL DEFAULT 'USER_SELECTED',
  source_rule_id VARCHAR(64),
  evidence_count INTEGER NOT NULL DEFAULT 0,
  session_count INTEGER NOT NULL DEFAULT 0,
  data_start_time VARCHAR(32),
  data_end_time VARCHAR(32),
  data_as_of VARCHAR(32) NOT NULL DEFAULT '',
  batch_id VARCHAR(64) NOT NULL DEFAULT '',
  content_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
  idempotency_key VARCHAR(128) NOT NULL DEFAULT '',
  status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
  version INTEGER NOT NULL DEFAULT 1,
  created_by VARCHAR(128) NOT NULL DEFAULT '',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (owner_user_id, idempotency_key)
)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_improvement_owner_bot ON insight_improvement_item (owner_user_id, bot_id, status, gmt_create)`,
      `CREATE TABLE IF NOT EXISTS insight_improvement_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  improvement_id INTEGER NOT NULL,
  session_id VARCHAR(128) NOT NULL DEFAULT '',
  task_index INTEGER NOT NULL DEFAULT 0,
  ordinal INTEGER NOT NULL DEFAULT 0,
  task_description_snapshot VARCHAR(1000) NOT NULL DEFAULT '',
  failure_class_snapshot VARCHAR(64) NOT NULL DEFAULT '',
  reasoning_summary VARCHAR(1000),
  payload_ref VARCHAR(512) NOT NULL DEFAULT '',
  payload_etag VARCHAR(128) NOT NULL DEFAULT '',
  payload_version_id VARCHAR(128),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (improvement_id, session_id, task_index)
)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_evidence_session_task ON insight_improvement_evidence (session_id, task_index)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_evidence_improvement_order ON insight_improvement_evidence (improvement_id, ordinal)`,
      `CREATE TABLE IF NOT EXISTS insight_improvement_evolve_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  improvement_id INTEGER NOT NULL,
  evolve_task_id VARCHAR(64) NOT NULL DEFAULT '',
  request_id VARCHAR(128) NOT NULL DEFAULT '',
  created_by VARCHAR(128) NOT NULL DEFAULT '',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (improvement_id, evolve_task_id),
  UNIQUE (improvement_id, request_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_evolve_task_id ON insight_improvement_evolve_link (evolve_task_id)`,
    ],
  },

  {
    version: 76,
    description: "Add Insight Center DB-backed failure task index",
    sql: [
      `CREATE TABLE IF NOT EXISTS insight_failure_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_dt VARCHAR(8) NOT NULL DEFAULT '',
  owner_user_id VARCHAR(128) NOT NULL DEFAULT '',
  bot_id VARCHAR(128) NOT NULL DEFAULT '',
  bot_name VARCHAR(256) NOT NULL DEFAULT '',
  session_id VARCHAR(128) NOT NULL DEFAULT '',
  task_index INTEGER NOT NULL DEFAULT 0,
  task_description VARCHAR(1000) NOT NULL DEFAULT '',
  is_complete INTEGER NOT NULL DEFAULT 2,
  failure_class VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
  judge_reason_summary VARCHAR(1000),
  session_start_time VARCHAR(32),
  session_end_time VARCHAR(32),
  session_duration_seconds INTEGER,
  is_cron INTEGER NOT NULL DEFAULT 0,
  payload_ref VARCHAR(512) NOT NULL DEFAULT '',
  payload_etag VARCHAR(128) NOT NULL DEFAULT '',
  payload_version_id VARCHAR(128),
  batch_id VARCHAR(64) NOT NULL DEFAULT '',
  data_as_of VARCHAR(32) NOT NULL DEFAULT '',
  judged_at VARCHAR(32),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (owner_user_id, session_id, task_index)
)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_failure_owner_dt ON insight_failure_task (owner_user_id, source_dt, is_complete)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_failure_owner_bot_dt ON insight_failure_task (owner_user_id, bot_id, source_dt, is_complete)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_failure_session_task ON insight_failure_task (owner_user_id, session_id, task_index)`,
    ],
  },

  {
    version: 77,
    description: "Add Insight Center DB-backed daily metric snapshots",
    sql: [
      `CREATE TABLE IF NOT EXISTS insight_metric_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_dt VARCHAR(8) NOT NULL DEFAULT '',
  owner_user_id VARCHAR(128) NOT NULL DEFAULT '',
  bot_id VARCHAR(128) NOT NULL DEFAULT '',
  bot_name VARCHAR(256) NOT NULL DEFAULT '',
  is_cron INTEGER NOT NULL DEFAULT 0,
  total_task_count DECIMAL(20,6) NOT NULL DEFAULT 0,
  valid_task_count DECIMAL(20,6) NOT NULL DEFAULT 0,
  complete_task_count DECIMAL(20,6) NOT NULL DEFAULT 0,
  capability_task_count DECIMAL(20,6) NOT NULL DEFAULT 0,
  capability_complete_task_count DECIMAL(20,6) NOT NULL DEFAULT 0,
  auto_complete_task_count DECIMAL(20,6) NOT NULL DEFAULT 0,
  failure_distribution_json TEXT DEFAULT NULL,
  batch_id VARCHAR(64) NOT NULL DEFAULT '',
  data_as_of VARCHAR(32) NOT NULL DEFAULT '',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (owner_user_id, bot_id, source_dt, is_cron)
)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_metric_owner_dt ON insight_metric_daily (owner_user_id, source_dt, is_cron)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_metric_owner_bot_dt ON insight_metric_daily (owner_user_id, bot_id, source_dt, is_cron)`,
    ],
  },

  {
    version: 78,
    description: "Align local Insight improvement tables with the streamlined evidence contract",
    sqliteOnly: true,
    sql: [
      `ALTER TABLE insight_improvement_evidence DROP COLUMN message_start`,
      `ALTER TABLE insight_improvement_evidence DROP COLUMN message_end`,
      `ALTER TABLE insight_improvement_evidence DROP COLUMN source_dt`,
      `ALTER TABLE insight_improvement_evidence DROP COLUMN batch_id`,
      `ALTER TABLE insight_improvement_evidence ADD COLUMN gmt_modified INTEGER NOT NULL DEFAULT 0`,
      `UPDATE insight_improvement_evidence SET gmt_modified = gmt_create WHERE gmt_modified = 0`,
      `ALTER TABLE insight_improvement_evolve_link ADD COLUMN gmt_modified INTEGER NOT NULL DEFAULT 0`,
      `UPDATE insight_improvement_evolve_link SET gmt_modified = gmt_create WHERE gmt_modified = 0`,
    ],
  },

  {
    version: 79,
    description: "Add Insight improvement Apply result audit fields",
    sql: [
      `ALTER TABLE insight_improvement_item ADD COLUMN applied_evolve_task_id VARCHAR(64)`,
      `ALTER TABLE insight_improvement_item ADD COLUMN apply_request_id VARCHAR(128)`,
      `ALTER TABLE insight_improvement_item ADD COLUMN applied_by VARCHAR(128)`,
      `ALTER TABLE insight_improvement_item ADD COLUMN applied_at TIMESTAMP`,
    ],
  },

  {
    version: 80,
    description: "Separate Insight improvement assignee from Bot owner",
    sql: [
      `ALTER TABLE insight_improvement_item ADD COLUMN bot_owner_user_id VARCHAR(128) NOT NULL DEFAULT ''`,
      `UPDATE insight_improvement_item SET bot_owner_user_id = owner_user_id WHERE bot_owner_user_id = ''`,
    ],
  },

  {
    version: 81,
    description: "Add optimistic locking version column to node_executions",
    sql: [
      `ALTER TABLE node_executions ADD COLUMN version BIGINT NOT NULL DEFAULT 1`,
    ],
  },
  {
    version: 82,
    description: "Create workflow_health_snapshots table for health score trend tracking",
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

  // Version 83 was used by a reverted self-repair schema change. Do not reuse it;
  // the next real migration must start at version 84 for environment compatibility.
  {
    version: 84,
    description: "Add Evolve frozen task source ledger",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_task_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id VARCHAR(64) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_id VARCHAR(128) NOT NULL DEFAULT '',
  source_schema_version VARCHAR(64) NOT NULL,
  adapter_version VARCHAR(64),
  source_ref_json MEDIUMTEXT NOT NULL,
  source_digest VARCHAR(80),
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  error_code VARCHAR(128),
  error_message TEXT,
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (task_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_task_sources_origin ON ce_task_sources (source_type, source_id)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_task_sources_status ON ce_task_sources (status, gmt_create)`,
    ],
  },
  {
    version: 85,
    description: "Add MySQL indexes for Evolve task source ledger",
    mysqlOnly: true,
    sql: [
      // v84 already created these indexes via CREATE INDEX IF NOT EXISTS (rendered as
      // CREATE INDEX for MySQL). No further DDL is needed for MySQL here.
    ],
  },
  {
    version: 86,
    description: "Add scoped Insight governance trust and persistent auto-repair grants",
    sql: [
      `CREATE TABLE IF NOT EXISTS insight_governance_rule_trust (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_fingerprint VARCHAR(64) NOT NULL COMMENT '规则快照指纹',
  environment VARCHAR(32) NOT NULL COMMENT '运行环境',
  source_rule_id VARCHAR(64) NOT NULL COMMENT '治理规则ID',
  rule_version INTEGER NOT NULL COMMENT '治理规则版本',
  action_type VARCHAR(32) NOT NULL COMMENT '治理动作类型',
  allowed_targets_json MEDIUMTEXT NOT NULL COMMENT '允许修改目标快照',
  risk VARCHAR(16) NOT NULL COMMENT '风险等级快照',
  status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE或REVOKED',
  trusted_by VARCHAR(128) NOT NULL COMMENT '信任操作人',
  trusted_at TIMESTAMP NOT NULL COMMENT '信任时间',
  version INTEGER NOT NULL DEFAULT 1 COMMENT '乐观锁版本',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '修改时间',
  UNIQUE (scope_fingerprint)
)`,
      `CREATE TABLE IF NOT EXISTS insight_auto_repair_grant (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_fingerprint VARCHAR(64) NOT NULL COMMENT '用户授权范围指纹',
  owner_user_id VARCHAR(128) NOT NULL COMMENT '授权用户',
  bot_id VARCHAR(128) NOT NULL COMMENT '授权Bot',
  environment VARCHAR(32) NOT NULL COMMENT '运行环境',
  source_rule_id VARCHAR(64) NOT NULL COMMENT '治理规则ID',
  rule_version INTEGER NOT NULL COMMENT '治理规则版本',
  action_type VARCHAR(32) NOT NULL COMMENT '治理动作类型',
  allowed_targets_json MEDIUMTEXT NOT NULL COMMENT '允许修改目标快照',
  risk VARCHAR(16) NOT NULL COMMENT '风险等级快照',
  status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE或REVOKED',
  source_improvement_id BIGINT NOT NULL COMMENT '授权来源改进项ID',
  granted_by VARCHAR(128) NOT NULL COMMENT '授权操作人',
  granted_at TIMESTAMP NOT NULL COMMENT '授权时间',
  revoked_by VARCHAR(128) COMMENT '撤销操作人',
  revoked_at TIMESTAMP COMMENT '撤销时间',
  version INTEGER NOT NULL DEFAULT 1 COMMENT '乐观锁版本',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '修改时间',
  UNIQUE (scope_fingerprint)
)`,
    ],
  },
  {
    version: 87,
    description: "Add Repair tool-call audit and execution ledger",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_repair_tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id VARCHAR(64) NOT NULL COMMENT '工具调用ID',
  task_id VARCHAR(64) NOT NULL COMMENT 'CE Task ID',
  step_id VARCHAR(64) NOT NULL COMMENT 'CE Step ID',
  execution_id VARCHAR(64) NOT NULL COMMENT 'AIS execution ID',
  authorization_scope_digest VARCHAR(64) NOT NULL COMMENT '用户Bot环境授权范围摘要',
  client_request_id VARCHAR(128) NOT NULL COMMENT 'Agent稳定幂等键',
  tool_name VARCHAR(64) NOT NULL COMMENT '类型化工具名',
  operation VARCHAR(256) NOT NULL COMMENT '类型化操作名',
  action_id VARCHAR(128) COMMENT '获批Plan动作ID',
  deadline_at BIGINT COMMENT '调用截止时间Unix秒',
  request_json MEDIUMTEXT NOT NULL COMMENT '脱敏且受限的执行请求或OSS引用',
  is_write INTEGER NOT NULL DEFAULT 0 COMMENT '是否写操作',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '调用状态',
  lease_owner VARCHAR(128) COMMENT '当前执行租约持有者',
  lease_expires_at BIGINT COMMENT '执行租约截止时间Unix秒',
  result_json MEDIUMTEXT COMMENT '脱敏且受限的终态信封或OSS引用',
  result_digest VARCHAR(64) COMMENT '完整终态信封摘要',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '修改时间',
  UNIQUE (call_id),
  UNIQUE (step_id, client_request_id)
)`,
    ],
  },
  {
    version: 90,
    description: "Add first-class Evolve Pack registry",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_packs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  source_task_id VARCHAR(64) NOT NULL,
  source_step_id VARCHAR(64) NOT NULL,
  source_kind VARCHAR(16) NOT NULL,
  source_round INTEGER NOT NULL DEFAULT 0,
  artifact_ref VARCHAR(1000) NOT NULL,
  artifact_size BIGINT NOT NULL,
  artifact_sha256 VARCHAR(64) NOT NULL,
  artifact_content_type VARCHAR(128) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'available',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (pack_id),
  UNIQUE (source_task_id, source_step_id, source_kind, source_round)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_packs_user_bot_time ON ce_packs (user_id, bot_id, gmt_create)`,
    ],
  },
  {
    version: 91,
    description: "Add MySQL indexes for Evolve Pack registry",
    mysqlOnly: true,
    sql: [
      // v90 already created this index via CREATE INDEX IF NOT EXISTS (rendered as
      // CREATE INDEX for MySQL). No further DDL is needed for MySQL here.
    ],
  },
  {
    version: 92,
    description: "Backfill historical Evolve Packs — SQLite",
    sqliteOnly: true,
    sql: [
      `INSERT OR IGNORE INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status, gmt_create, gmt_modified)
       SELECT 'PACK-MIG-' || UPPER(HEX(RANDOMBLOB(16))), t.user_id, t.bot_id, t.task_id, s.step_id,
        'baseline', 0,
        json_extract(s.output_json, '$.baselineArtifact.artifact.ref'),
        json_extract(s.output_json, '$.baselineArtifact.artifact.size'),
        json_extract(s.output_json, '$.baselineArtifact.artifact.sha256'),
        json_extract(s.output_json, '$.baselineArtifact.artifact.contentType'),
        'available', COALESCE(s.completed_at, s.gmt_create), COALESCE(s.completed_at, s.gmt_create)
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.status = 'succeeded'
        AND s.step_type = 'optimize'
        AND s.round_no = 1
        AND json_extract(s.output_json, '$.baselineArtifact.status') = 'available'
        AND json_extract(s.output_json, '$.baselineArtifact.artifact.ref') IS NOT NULL`,
      `INSERT OR IGNORE INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status, gmt_create, gmt_modified)
       SELECT 'PACK-MIG-' || UPPER(HEX(RANDOMBLOB(16))), t.user_id, t.bot_id, t.task_id, s.step_id,
        CASE WHEN t.task_type = 'pack' THEN 'snapshot' ELSE 'round' END,
        CASE WHEN t.task_type = 'pack' THEN 0 ELSE COALESCE(s.round_no, 0) END,
        json_extract(s.output_json, '$.pack.artifact.ref'),
        json_extract(s.output_json, '$.pack.artifact.size'),
        json_extract(s.output_json, '$.pack.artifact.sha256'),
        json_extract(s.output_json, '$.pack.artifact.contentType'),
        'available', COALESCE(s.completed_at, s.gmt_create), COALESCE(s.completed_at, s.gmt_create)
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.status = 'succeeded'
        AND s.step_type IN ('optimize', 'pack')
        AND json_extract(s.output_json, '$.pack.status') = 'available'
        AND json_extract(s.output_json, '$.pack.artifact.ref') IS NOT NULL`,
    ],
  },
  {
    version: 93,
    description: "Backfill historical Evolve Packs — MySQL",
    mysqlOnly: true,
    sql: [
      `INSERT IGNORE INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status, gmt_create, gmt_modified)
       SELECT CONCAT('PACK-MIG-', UPPER(REPLACE(UUID(), '-', ''))), t.user_id, t.bot_id, t.task_id, s.step_id,
        'baseline', 0,
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.baselineArtifact.artifact.ref')),
        CAST(JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.baselineArtifact.artifact.size')) AS UNSIGNED),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.baselineArtifact.artifact.sha256')),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.baselineArtifact.artifact.contentType')),
        'available', COALESCE(s.completed_at, s.gmt_create), COALESCE(s.completed_at, s.gmt_create)
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.status = 'succeeded'
        AND s.step_type = 'optimize'
        AND s.round_no = 1
        AND JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.baselineArtifact.status')) = 'available'
        AND JSON_EXTRACT(s.output_json, '$.baselineArtifact.artifact.ref') IS NOT NULL`,
      `INSERT IGNORE INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status, gmt_create, gmt_modified)
       SELECT CONCAT('PACK-MIG-', UPPER(REPLACE(UUID(), '-', ''))), t.user_id, t.bot_id, t.task_id, s.step_id,
        CASE WHEN t.task_type = 'pack' THEN 'snapshot' ELSE 'round' END,
        CASE WHEN t.task_type = 'pack' THEN 0 ELSE COALESCE(s.round_no, 0) END,
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.ref')),
        CAST(JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.size')) AS UNSIGNED),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.sha256')),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.contentType')),
        'available', COALESCE(s.completed_at, s.gmt_create), COALESCE(s.completed_at, s.gmt_create)
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.status = 'succeeded'
        AND s.step_type IN ('optimize', 'pack')
        AND JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.status')) = 'available'
        AND JSON_EXTRACT(s.output_json, '$.pack.artifact.ref') IS NOT NULL`,
    ],
  },
  {
    version: 94,
    description: "ClawWeb: add state_json to flow_runs (engine state mirror for cross-instance reads)",
    sql: [
      // MEDIUMTEXT (16MB on MySQL; sqlite renders to TEXT) — TaskFlow state_json
      // routinely exceeds TEXT's 64KB MySQL cap once nodeStates carry results.
      `ALTER TABLE flow_runs ADD COLUMN state_json MEDIUMTEXT`,
    ],
  },
  {
    version: 95,
    description: "ClawWeb: flow_retry_requests mailbox for cross-engine retry handoff",
    sql: [
      `CREATE TABLE IF NOT EXISTS flow_retry_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  reason TEXT,
  requester VARCHAR(255),
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  claim_engine VARCHAR(255),
  claimed_at BIGINT,
  attempts INTEGER NOT NULL DEFAULT 0,
  result_message TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_flow_retry_requests_request_id ON flow_retry_requests (request_id)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_retry_requests_status ON flow_retry_requests (status)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_retry_requests_flow_id ON flow_retry_requests (flow_id)`,
    ],
  },
  {
    version: 96,
    description: "Add clawweb_admin_users table for dynamic admin permission management",
    sql: [
      `CREATE TABLE IF NOT EXISTS clawweb_admin_users (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  user_id VARCHAR(255) NOT NULL COMMENT '管理员用户标识(工号或域账号)',
  role VARCHAR(32) NOT NULL COMMENT '管理员角色: admin|log_admin|bench_admin|claw_evolve_admin',
  source VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT '来源: yaml_seed|manual',
  enabled TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用: 1启用, 0禁用',
  created_by VARCHAR(255) DEFAULT NULL COMMENT '创建者用户标识',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_clawweb_admin_users_user_role (user_id, role),
  INDEX idx_clawweb_admin_users_role (role),
  INDEX idx_clawweb_admin_users_enabled (enabled),
  INDEX idx_clawweb_admin_users_user_id (user_id)
) COMMENT='ClawWeb动态管理员权限表'`,
    ],
  },
  {
    version: 97,
    description: "Backfill missed Optimize Round Packs — SQLite",
    sqliteOnly: true,
    sql: [
      `INSERT OR IGNORE INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status, gmt_create, gmt_modified)
       SELECT 'PACK-MIG-' || UPPER(HEX(RANDOMBLOB(16))), t.user_id, t.bot_id, t.task_id, s.step_id,
        'round', COALESCE(s.round_no, 0),
        json_extract(s.output_json, '$.pack.artifact.ref'),
        json_extract(s.output_json, '$.pack.artifact.size'),
        json_extract(s.output_json, '$.pack.artifact.sha256'),
        json_extract(s.output_json, '$.pack.artifact.contentType'),
        'available', COALESCE(s.completed_at, s.gmt_create), COALESCE(s.completed_at, s.gmt_create)
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.status = 'succeeded'
        AND s.step_type = 'optimize'
        AND json_extract(s.output_json, '$.pack.status') = 'available'
        AND json_extract(s.output_json, '$.pack.artifact.ref') IS NOT NULL`,
    ],
  },
  {
    version: 98,
    description: "Backfill missed Optimize Round Packs — MySQL",
    mysqlOnly: true,
    sql: [
      `INSERT IGNORE INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status, gmt_create, gmt_modified)
       SELECT CONCAT('PACK-MIG-', UPPER(REPLACE(UUID(), '-', ''))), t.user_id, t.bot_id, t.task_id, s.step_id,
        'round', COALESCE(s.round_no, 0),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.ref')),
        CAST(JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.size')) AS UNSIGNED),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.sha256')),
        JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.artifact.contentType')),
        'available', COALESCE(s.completed_at, s.gmt_create), COALESCE(s.completed_at, s.gmt_create)
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE s.status = 'succeeded'
        AND s.step_type = 'optimize'
        AND JSON_UNQUOTE(JSON_EXTRACT(s.output_json, '$.pack.status')) = 'available'
        AND JSON_EXTRACT(s.output_json, '$.pack.artifact.ref') IS NOT NULL`,
    ],
  },
  {
    version: 99,
    description: "Remove non-Round-1 Initial Pack registrations — SQLite",
    sqliteOnly: true,
    sql: [
      `DELETE FROM ce_packs
       WHERE source_kind = 'baseline'
        AND EXISTS (
          SELECT 1 FROM ce_steps s
          WHERE s.task_id = ce_packs.source_task_id
           AND s.step_id = ce_packs.source_step_id
           AND (s.step_type <> 'optimize' OR COALESCE(s.round_no, 0) <> 1)
        )`,
    ],
  },
  {
    version: 100,
    description: "Remove non-Round-1 Initial Pack registrations — MySQL",
    mysqlOnly: true,
    sql: [
      `DELETE p FROM ce_packs p
       INNER JOIN ce_steps s
        ON s.task_id = p.source_task_id AND s.step_id = p.source_step_id
       WHERE p.source_kind = 'baseline'
        AND (s.step_type <> 'optimize' OR COALESCE(s.round_no, 0) <> 1)`,
    ],
  },
  {
    version: 99,
    description: "Add Governance rule evolution proposals",
    sql: [
      `CREATE TABLE IF NOT EXISTS insight_rule_evolution_proposal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_fingerprint VARCHAR(64) NOT NULL COMMENT '规则快照指纹',
  environment VARCHAR(32) NOT NULL COMMENT '运行环境',
  source_rule_id VARCHAR(64) NOT NULL COMMENT '治理规则ID',
  from_rule_version INTEGER NOT NULL COMMENT '当前规则版本',
  proposed_rule_version INTEGER NOT NULL COMMENT '建议发布的规则版本',
  proposal_type VARCHAR(32) NOT NULL COMMENT '规则演进类型',
  action_type VARCHAR(32) NOT NULL COMMENT '治理动作类型',
  allowed_targets_json MEDIUMTEXT NOT NULL COMMENT '允许修改目标快照',
  risk VARCHAR(16) NOT NULL COMMENT '风险等级快照',
  success_count INTEGER NOT NULL DEFAULT 0 COMMENT '成功验收次数',
  owner_count INTEGER NOT NULL DEFAULT 0 COMMENT '覆盖Owner数量',
  bot_count INTEGER NOT NULL DEFAULT 0 COMMENT '覆盖Bot数量',
  last_verified_at TIMESTAMP NULL COMMENT '最近一次成功验收时间',
  rationale MEDIUMTEXT NOT NULL COMMENT '规则演进依据',
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING、APPROVED或REJECTED',
  reviewed_by VARCHAR(128) COMMENT '审核人',
  reviewed_at TIMESTAMP NULL COMMENT '审核时间',
  review_comment MEDIUMTEXT COMMENT '审核说明',
  version INTEGER NOT NULL DEFAULT 1 COMMENT '乐观锁版本',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (scope_fingerprint, proposal_type, from_rule_version)
)`,
      `CREATE INDEX IF NOT EXISTS idx_insight_rule_evolution_status ON insight_rule_evolution_proposal (status, gmt_modified)`,
    ],
  },
  {
    version: 100,
    description: "Add http_callback_logs.callback_source column to distinguish workflow-level vs platform-level callbacks",
    sql: [
      `ALTER TABLE http_callback_logs ADD COLUMN callback_source VARCHAR(32) NOT NULL DEFAULT 'workflow-level'`,
      `CREATE INDEX IF NOT EXISTS idx_http_callback_logs_source ON http_callback_logs (callback_source)`,
    ],
  },
  {
    version: 101,
    description: "Add independent ClawEvolve task log archive records",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_task_log_archives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        archive_id VARCHAR(128) NOT NULL UNIQUE,
        task_id VARCHAR(128) NOT NULL,
        active_key VARCHAR(128) UNIQUE,
        status VARCHAR(32) NOT NULL,
        requested_by VARCHAR(128) NOT NULL,
        transport VARCHAR(32),
        bot_run_id VARCHAR(256),
        bot_session_id VARCHAR(256),
        platform_response_json MEDIUMTEXT,
        artifact_ref MEDIUMTEXT,
        artifact_size BIGINT,
        artifact_sha256 VARCHAR(128),
        artifact_content_type VARCHAR(128),
        metadata_json MEDIUMTEXT,
        error_code VARCHAR(128),
        error_message MEDIUMTEXT,
        started_at BIGINT,
        completed_at BIGINT,
        gmt_create BIGINT NOT NULL,
        gmt_modified BIGINT NOT NULL
      )`,
      `CREATE INDEX IF NOT EXISTS idx_ce_task_log_archives_task_time ON ce_task_log_archives (task_id, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_task_log_archives_task_status ON ce_task_log_archives (task_id, status)`,
    ],
  },
  {
    version: 102,
    description: "Add is_active column to workflow_deploy_history for default version routing",
    mysqlOnly: true,
    sql: [
      `ALTER TABLE workflow_deploy_history ADD COLUMN is_active TINYINT NOT NULL DEFAULT 0`,
      `CREATE INDEX IF NOT EXISTS idx_wdh_workflow_active ON workflow_deploy_history (workflow_id, is_active)`,
    ],
  },
  {
    version: 103,
    description: "SQLite: create workflow_deploy_history table with is_active column + add version column to workflow_specs (MySQL got these in v64+v102)",
    sqliteOnly: true,
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_deploy_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  deploy_number INTEGER NOT NULL,
  version INTEGER NOT NULL,
  tag_name VARCHAR(255),
  action VARCHAR(32) NOT NULL,
  from_deploy_number INTEGER,
  spec_json TEXT NOT NULL,
  note VARCHAR(512),
  bot_id VARCHAR(255),
  owner_id VARCHAR(255),
  is_active INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_wdh_pack_deploy_wf ON workflow_deploy_history (pack_id, deploy_number, workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_wdh_workflow_version ON workflow_deploy_history (workflow_id, version)`,
      `CREATE INDEX IF NOT EXISTS idx_wdh_workflow_active ON workflow_deploy_history (workflow_id, is_active)`,
      // SQLite also needs workflow_specs.version column (MySQL got it in v64)
      `ALTER TABLE workflow_specs ADD COLUMN version INTEGER`,
      `CREATE INDEX IF NOT EXISTS idx_wfs_workflow_version ON workflow_specs (workflow_id, version)`,
    ],
  },
  {
    version: 104,
    description: "Add workflow_version and workflow_deploy_number to flow_runs for multi-version tracking",
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN workflow_version INTEGER`,
      `ALTER TABLE flow_runs ADD COLUMN workflow_deploy_number INTEGER`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_wf_version ON flow_runs (workflow_id, workflow_version)`,
    ],
  },
  {
    version: 105,
    description: "Repair missing ClawEvolve task log archive storage",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_task_log_archives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        archive_id VARCHAR(128) NOT NULL UNIQUE,
        task_id VARCHAR(128) NOT NULL,
        active_key VARCHAR(128) UNIQUE,
        status VARCHAR(32) NOT NULL,
        requested_by VARCHAR(128) NOT NULL,
        transport VARCHAR(32),
        bot_run_id VARCHAR(256),
        bot_session_id VARCHAR(256),
        platform_response_json MEDIUMTEXT,
        artifact_ref MEDIUMTEXT,
        artifact_size BIGINT,
        artifact_sha256 VARCHAR(128),
        artifact_content_type VARCHAR(128),
        metadata_json MEDIUMTEXT,
        error_code VARCHAR(128),
        error_message MEDIUMTEXT,
        started_at BIGINT,
        completed_at BIGINT,
        gmt_create BIGINT NOT NULL,
        gmt_modified BIGINT NOT NULL
      )`,
      `CREATE INDEX IF NOT EXISTS idx_ce_task_log_archives_task_time ON ce_task_log_archives (task_id, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_task_log_archives_task_status ON ce_task_log_archives (task_id, status)`,
    ],
  },
  {
    version: 106,
    description: "Add owner-confirmed auto execution flag for scoped Insight repair grants",
    sql: [
      `ALTER TABLE insight_auto_repair_grant ADD COLUMN auto_execute INTEGER NOT NULL DEFAULT 0`,
      `CREATE INDEX IF NOT EXISTS idx_insight_auto_grant_auto_execute ON insight_auto_repair_grant (owner_user_id, bot_id, source_rule_id, status, auto_execute)`,
    ],
  },
  {
    version: 107,
    description: "Store verified repair guidance with governance rule evolution proposals",
    sql: [
      `ALTER TABLE insight_rule_evolution_proposal ADD COLUMN learned_fix TEXT`,
    ],
  },
  {
    version: 108,
    description: "Self-Evolution: lessons + suggestion_outcomes + failure_signatures + diagnosis_cards + repair_history + weakness_list + node_executions 补列 + node_attempts",
    sql: [
      `CREATE TABLE IF NOT EXISTS lessons (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  failure_signature VARCHAR(256) NOT NULL COMMENT '失败签名:错误类型×节点类型×工具名',
  error_class VARCHAR(64) NOT NULL DEFAULT '' COMMENT '一级错误类目: param-type-mismatch|timeout|tool-not-found|contract-violation|auth|dependency-down|prompt-ambiguity',
  executor_type VARCHAR(64) NOT NULL DEFAULT '' COMMENT '执行器类型: embedded-agent|mcp-call|cli-script|...',
  tool_or_node VARCHAR(128) NOT NULL DEFAULT '' COMMENT '工具名或节点名',
  repair_type VARCHAR(32) NOT NULL COMMENT '修法类型: kb_hint|prompt_patch|arg_template_fix|node_patch|alert',
  repair_content MEDIUMTEXT NOT NULL COMMENT '修法内容(补丁后prompt/参数模板/节点结构调整)',
  confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0.5000 COMMENT '可信度评分 0.0-1.0',
  hit_count INTEGER NOT NULL DEFAULT 0 COMMENT '命中总次数',
  success_count INTEGER NOT NULL DEFAULT 0 COMMENT '命中且成功次数',
  fail_count INTEGER NOT NULL DEFAULT 0 COMMENT '命中且失败次数',
  status VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT '状态: draft|validated|live|expired|suspended',
  evidence_run_ids TEXT COMMENT '支撑该经验的flowId列表(JSON)',
  source VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT '来源: manual|auto_heal|runtime_retry|batch_patch|auto_release',
  related_workflow_ids TEXT COMMENT '关联工作流ID列表(JSON)',
  metrics_before TEXT COMMENT '应用修法前的指标快照(JSON)',
  metrics_after TEXT COMMENT '应用修法后的指标快照(JSON)',
  bench_domain_id INTEGER COMMENT '评测域ID',
  last_hit_at INTEGER COMMENT '最近一次命中时间(unix秒)',
  last_hit_success INTEGER COMMENT '最近一次命中是否成功 0=否 1=是',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_lessons_sig_type (failure_signature, repair_type),
  INDEX idx_lessons_sig (failure_signature),
  INDEX idx_lessons_status (status, confidence_score),
  INDEX idx_lessons_hit (last_hit_at)
) COMMENT='经验库: 系统记忆故障修复经验'`,

      `CREATE TABLE IF NOT EXISTS suggestion_outcomes (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  lesson_id BIGINT NOT NULL COMMENT '关联的lessons.id',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) NOT NULL DEFAULT '' COMMENT '节点ID',
  failure_signature VARCHAR(256) NOT NULL COMMENT '失败签名',
  adopted INTEGER NOT NULL DEFAULT 0 COMMENT '是否被采纳 1=是 0=否',
  applied_version VARCHAR(64) COMMENT '应用版本号',
  metrics_before TEXT COMMENT '应用前指标(JSON)',
  metrics_after TEXT COMMENT '应用后指标(JSON)',
  verdict VARCHAR(16) NOT NULL COMMENT '效果判定: improved|regressed|neutral|improved_after_retry',
  source VARCHAR(32) NOT NULL COMMENT '来源: runtime_retry|batch_patch|auto_release',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_outcome_lesson_wf_node_lesson (lesson_id, workflow_id, node_id),
  INDEX idx_outcome_lesson (lesson_id),
  INDEX idx_outcome_sig (failure_signature),
  INDEX idx_outcome_verdict (verdict, gmt_create)
) COMMENT='强化信号表: 跨快慢两环的成绩单'`,

      `CREATE TABLE IF NOT EXISTS failure_signatures (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  failure_signature VARCHAR(256) NOT NULL COMMENT '失败签名: 错误类型×节点类型×工具名',
  error_class VARCHAR(64) NOT NULL DEFAULT '' COMMENT '一级错误类目',
  executor_type VARCHAR(64) NOT NULL DEFAULT '' COMMENT '执行器类型',
  tool_or_node VARCHAR(128) NOT NULL DEFAULT '' COMMENT '工具名或节点名',
  raw_error_sample VARCHAR(500) COMMENT '原始错误摘要(用于人工确认)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_fs_flow_node (flow_id, node_id),
  INDEX idx_fs_sig (failure_signature)
) COMMENT='失败签名索引: 快速提取错误类型×节点×工具'`,

      `CREATE TABLE IF NOT EXISTS diagnosis_cards (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  failure_signature VARCHAR(256) NOT NULL COMMENT '失败签名',
  error_text TEXT NOT NULL COMMENT '错误文本',
  input_snapshot MEDIUMTEXT COMMENT '输入快照(JSON)',
  output_snapshot MEDIUMTEXT COMMENT '输出快照(JSON)',
  step_traces_snapshot MEDIUMTEXT COMMENT '步骤轨迹快照(JSON)',
  analysis_reasoning MEDIUMTEXT COMMENT '分析推理过程',
  suggested_repair_type VARCHAR(32) COMMENT '建议修法类型',
  suggested_repair_content MEDIUMTEXT COMMENT '建议修法内容',
  matched_lesson_id INTEGER COMMENT '命中的经验ID',
  outcome VARCHAR(16) NOT NULL DEFAULT 'not_recovered' COMMENT '结果: not_recovered|recovered',
  attempt_count INTEGER NOT NULL DEFAULT 0 COMMENT '总尝试次数',
  diagnosis_level VARCHAR(8) NOT NULL DEFAULT '' COMMENT '触发层级: L1|L2|L3',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_diag_sig (failure_signature),
  INDEX idx_diag_outcome (outcome),
  INDEX idx_diag_wf (workflow_id, gmt_create),
  INDEX idx_diag_hit (matched_lesson_id)
) COMMENT='诊断卡: 单次运行分析产物落库'`,

      `CREATE TABLE IF NOT EXISTS repair_history (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  failure_signature VARCHAR(256) NOT NULL COMMENT '失败签名',
  lesson_id INTEGER COMMENT '应用的经验ID',
  diagnosis_card_id INTEGER COMMENT '关联诊断卡ID',
  suggestion_outcome_id INTEGER COMMENT '关联强化信号ID',
  repair_type VARCHAR(32) NOT NULL COMMENT '修法类型: kb_hint|prompt_patch|arg_template_fix|node_patch|alert',
  repair_content MEDIUMTEXT COMMENT '修法内容',
  applied_by VARCHAR(32) NOT NULL COMMENT '应用方: guardian|auto_heal|evolution|manual',
  retry_success INTEGER COMMENT '重试是否成功 0=否 1=是 NULL=未尝试',
  level VARCHAR(8) NOT NULL DEFAULT '' COMMENT '触发层级: L1|L2|L3',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_repair_sig (failure_signature, gmt_create),
  INDEX idx_repair_success (retry_success, gmt_create),
  INDEX idx_repair_applied (applied_by, gmt_create)
) COMMENT='修复记录: 修法×效果×来源账本'`,

      `CREATE TABLE IF NOT EXISTS weakness_list (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  failure_signature VARCHAR(256) NOT NULL COMMENT '失败签名(全局唯一)',
  error_class VARCHAR(64) NOT NULL DEFAULT '' COMMENT '一级错误类目',
  workflow_ids MEDIUMTEXT COMMENT '出现该弱点的workflowId列表(JSON数组)',
  occurrence_count INTEGER NOT NULL DEFAULT 0 COMMENT '出现总次数',
  affected_workflows_count INTEGER NOT NULL DEFAULT 0 COMMENT '受影响工作流数',
  repairability VARCHAR(16) NOT NULL DEFAULT 'auto' COMMENT '可修性: auto|semi|manual',
  priority_score DECIMAL(5,2) NOT NULL DEFAULT 0.00 COMMENT '优先级=log(occurrence+1)×affected_workflows_count',
  evidence_diagnosis_ids MEDIUMTEXT COMMENT '证据诊断卡ID列表(JSON数组)',
  latest_occurrence INTEGER COMMENT '最近一次发生时间(unix秒)',
  first_occurrence INTEGER COMMENT '首次发生时间(unix秒)',
  matched_lesson_ids MEDIUMTEXT COMMENT '已关联的经验ID列表(JSON数组)',
  status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '状态: active|resolved|closed',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_weak_sig (failure_signature),
  INDEX idx_weak_priority (priority_score, status),
  INDEX idx_weak_status (status)
) COMMENT='弱点清单: batch分析跨run的ranking'`,

      `ALTER TABLE node_executions ADD COLUMN failure_signature VARCHAR(256) COMMENT '失败签名(自进化v108补列)'`,
      `ALTER TABLE node_executions ADD COLUMN repair_strategy VARCHAR(32) COMMENT '修法类型: kb_hint|prompt_patch|arg_template_fix|node_patch|alert'`,
      `ALTER TABLE node_executions ADD COLUMN is_recoverable INTEGER NOT NULL DEFAULT 0 COMMENT '是否可恢复 0=否 1=是'`,
      `ALTER TABLE node_executions ADD COLUMN matched_lesson_id INTEGER COMMENT '命中的经验ID'`,
      `ALTER TABLE node_executions ADD COLUMN resolved_args_json MEDIUMTEXT COMMENT '实际下发的参数模板(JSON)'`,
      `CREATE INDEX IF NOT EXISTS idx_node_exec_fs ON node_executions (failure_signature)`,

      `CREATE TABLE IF NOT EXISTS node_attempts (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  attempt INTEGER NOT NULL COMMENT '第几次重试(0-based)',
  error_text MEDIUMTEXT COMMENT '该次尝试的错误文本',
  duration_ms BIGINT COMMENT '该次尝试耗时(毫秒)',
  kb_lesson_ids_json MEDIUMTEXT COMMENT 'L1召回命中的lessonId列表(JSON)',
  did_succeed INTEGER NOT NULL DEFAULT 0 COMMENT '该次尝试是否成功 0=否 1=是',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_att_flow_node_attempt (flow_id, node_id, attempt),
  INDEX idx_att_flow_node (flow_id, node_id),
  CONSTRAINT fk_att_flow FOREIGN KEY (flow_id) REFERENCES flow_runs(flow_id) ON DELETE CASCADE
) COMMENT='节点重试留痕: append-only补充node_executions只保留latest的问题'`,
    ],
  },
];
