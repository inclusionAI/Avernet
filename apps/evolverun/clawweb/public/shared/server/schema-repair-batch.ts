import type { IDatabase } from './db.js';

/** Additive repair-v2 storage only. No release or task dispatch capability is enabled. */
export const repairBatchMigrations = [
  {
    version: 136,
    description: 'Workflow repair immutable items/revisions and lifecycle audit references',
    migrate: bootstrapHealingTables,
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_repair_items (
  item_id VARCHAR(64) PRIMARY KEY COMMENT '修复处理项唯一标识',
  workflow_id VARCHAR(255) NOT NULL COMMENT '所属Workflow唯一标识',
  group_key VARCHAR(128) NOT NULL COMMENT '稳定问题分组键',
  proposal_key VARCHAR(64) NOT NULL COMMENT '建议内容摘要键',
  episode_key VARCHAR(128) NOT NULL COMMENT '问题发生轮次键',
  identity_digest VARCHAR(64) NOT NULL COMMENT 'Workflow分组建议轮次组合身份SHA-256摘要',
  content_revision INTEGER NOT NULL COMMENT '处理项内容版本号',
  previous_item_id VARCHAR(64) COMMENT '上一轮关联处理项ID',
  content_json MEDIUMTEXT NOT NULL COMMENT '问题建议证据等冻结内容JSON',
  source_refs_json MEDIUMTEXT NOT NULL COMMENT '关联建议诊断及事件来源引用JSON',
  state VARCHAR(32) NOT NULL COMMENT '处理状态:pending|processing|awaiting_verification|verified|ineffective|no_action',
  state_version INTEGER NOT NULL DEFAULT 0 COMMENT '处理状态乐观锁版本号',
  active_task_id VARCHAR(64) COMMENT '当前占用该处理项的修复任务ID',
  active_revision INTEGER COMMENT '当前占用该处理项的修订版本号',
  disposition_json TEXT COMMENT '人工无需处理或恢复决定的当前投影JSON',
  updated_at_ms BIGINT NOT NULL COMMENT '业务内容最近更新时间Unix毫秒',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '记录创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '记录修改时间',
  UNIQUE INDEX uk_repair_item_identity (identity_digest),
  INDEX idx_repair_item_workflow (workflow_id),
  INDEX idx_repair_item_task (active_task_id)
) COMMENT='Workflow修复处理项'`,
      `CREATE TABLE IF NOT EXISTS workflow_repair_revisions (
  task_id VARCHAR(64) NOT NULL COMMENT '修复任务唯一标识',
  revision INTEGER NOT NULL COMMENT '任务修订版本号',
  workflow_id VARCHAR(255) NOT NULL COMMENT '所属Workflow唯一标识',
  phase VARCHAR(32) NOT NULL COMMENT '修订阶段:drafting|review|blocked|publishing|published|no_change|failed|cancelled',
  state_version INTEGER NOT NULL DEFAULT 0 COMMENT '修订状态乐观锁版本号',
  request_id VARCHAR(128) NOT NULL COMMENT '创建本修订的客户端幂等请求ID',
  request_digest VARCHAR(64) NOT NULL COMMENT '修订请求内容SHA-256摘要',
  request_key VARCHAR(64) NOT NULL COMMENT '任务与请求组合幂等键SHA-256摘要',
  input_json MEDIUMTEXT NOT NULL COMMENT '本修订冻结输入JSON',
  draft_json MEDIUMTEXT COMMENT '候选分支Commit及逐项处理结果JSON',
  checks_json MEDIUMTEXT COMMENT '候选静态和Mock检查结果JSON',
  candidate_digest VARCHAR(64) COMMENT '候选草稿规范化内容SHA-256摘要',
  checks_digest VARCHAR(64) COMMENT '检查报告规范化内容SHA-256摘要',
  confirmation_json TEXT COMMENT '人工审核确认内容JSON',
  error_json TEXT COMMENT '本修订失败错误信息JSON',
  created_at_ms BIGINT NOT NULL COMMENT '修订创建时间Unix毫秒',
  updated_at_ms BIGINT NOT NULL COMMENT '修订最近更新时间Unix毫秒',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '记录创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '记录修改时间',
  PRIMARY KEY (task_id, revision),
  UNIQUE INDEX uk_repair_revision_request (request_key),
  INDEX idx_repair_revision_workflow (workflow_id)
) COMMENT='Workflow修复任务修订'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN repair_item_id VARCHAR(64) COMMENT '关联修复处理项ID'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN repair_revision INTEGER COMMENT '关联修复任务修订版本号'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN deployed_version INTEGER COMMENT '本次处置关联的Workflow部署版本号'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN evidence_json TEXT COMMENT '处置审计证据JSON'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN event_key VARCHAR(128) COMMENT '处置审计事件幂等键'`,
    ],
  },
  {
    version: 137,
    description: 'Workflow repair SQLite audit indexes',
    sqliteOnly: true,
    sql: [
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_healing_outcome_event ON workflow_healing_outcomes (event_key)`,
      `CREATE INDEX IF NOT EXISTS idx_healing_outcome_repair_item ON workflow_healing_outcomes (repair_item_id, repair_revision)`,
    ],
  },
  {
    version: 138,
    description: 'Workflow repair MySQL/ZDAS task-independent audit compatibility and indexes',
    mysqlOnly: true,
    sql: [
      // v117 was SQLite-only. Do not assume production already has those nullable fields.
      `ALTER TABLE workflow_healing_outcomes MODIFY COLUMN lesson_id VARCHAR(64) NULL`,
      `ALTER TABLE workflow_healing_outcomes MODIFY COLUMN workflow_id VARCHAR(255) NULL`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN suggestion_id VARCHAR(64) COMMENT '关联优化建议ID'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN source_task_id VARCHAR(64) COMMENT '来源任务ID'`,
      `ALTER TABLE workflow_healing_outcomes ADD COLUMN source_step_id VARCHAR(64) COMMENT '来源任务步骤ID'`,
      `ALTER TABLE workflow_healing_outcomes ADD UNIQUE INDEX uk_healing_outcome_event (event_key)`,
      `ALTER TABLE workflow_healing_outcomes ADD INDEX idx_healing_outcome_repair_item (repair_item_id, repair_revision)`,
    ],
  },
];

export const healingBootstrapSql = [
  // v110-v118 were SQLite-only. Repair reads diagnoses/suggestions and writes
  // outcomes, so bootstrap their dependencies too, including later fields.
  // IF NOT EXISTS preserves existing tables; inline indexes also work on ZDAS.
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
  source VARCHAR(32) DEFAULT 'log_analysis',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (lesson_id),
  INDEX idx_workflow_healing_lessons_workflow (workflow_id, status, gmt_create),
  INDEX idx_workflow_healing_lessons_signature (failure_signature, failure_mode)
)`,
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
  UNIQUE (diagnosis_id),
  INDEX idx_workflow_healing_diagnoses_workflow (workflow_id, gmt_create),
  INDEX idx_workflow_healing_diagnoses_flow (flow_id, gmt_create)
)`,
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
  UNIQUE (workflow_id, signature),
  INDEX idx_wh_suggestion_actions_wf (workflow_id, gmt_create)
)`,
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
  proposal_json MEDIUMTEXT COMMENT '结构化修复建议JSON',
  proposal_digest VARCHAR(64) COMMENT '结构化建议摘要',
  apply_task_id VARCHAR(64) COMMENT '建议应用任务ID',
  created_by VARCHAR(128) COMMENT '创建人',
  updated_by VARCHAR(128) COMMENT '更新人',
  applied_at INTEGER DEFAULT NULL,
  verification_status VARCHAR(32) NOT NULL DEFAULT 'not_started',
  verification_checked_at INTEGER DEFAULT NULL,
  recurrence_count INTEGER NOT NULL DEFAULT 0,
  last_recurrence_at INTEGER DEFAULT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (workflow_id, failure_signature),
  INDEX idx_wh_suggestions_wf_status (workflow_id, status, gmt_create),
  INDEX idx_wh_suggestions_signature (workflow_id, failure_signature),
  INDEX idx_wh_suggestions_verification (verification_status, applied_at)
)`,
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
  UNIQUE (outcome_id),
  INDEX idx_workflow_healing_outcomes_lesson (lesson_id, gmt_create)
)`,
];

/** Create only absent healing tables before the repair audit ALTERs. */
async function bootstrapHealingTables(db: IDatabase): Promise<void> {
  for (const ddl of healingBootstrapSql) {
    const safe = db.dbType !== 'zdas' ? ddl : ddl
      // utf8mb4: (127 + 64) * 4 = 764 bytes, below ZDAS's 767-byte limit.
      .replace('INDEX idx_workflow_healing_lessons_signature (failure_signature, failure_mode)',
        'INDEX idx_workflow_healing_lessons_signature (failure_signature(127), failure_mode)')
      .replace('INDEX idx_wh_suggestions_signature (workflow_id, failure_signature)',
        'INDEX idx_wh_suggestions_signature (workflow_id, failure_signature(127))')
      // A prefix UNIQUE would reject distinct signatures sharing a long prefix.
      // Hash both complete values, with a length-delimited workflow identity.
      .replace(/UNIQUE \(workflow_id, (signature|failure_signature)\)/,
        (_, signature: string) => `signature_identity BINARY(32) GENERATED ALWAYS AS
    (UNHEX(SHA2(CONCAT(LENGTH(workflow_id), ':', workflow_id, ${signature}), 256))) STORED,
  UNIQUE (signature_identity)`);
    await db.exec(db.dialect.renderDdl(safe));
  }
}
