/**
 * Schema DDL for workflow engine tables.
 *
 * Uses SQLite-compatible SQL as the canonical dialect and adapts
 * to MySQL via the `adaptDdl` function.
 *
 * Compliance (ZDAS/MySQL):
 * - id columns use BIGINT (MySQL) / INTEGER (SQLite)
 * - gmt_create / gmt_modified required on every table
 * - No FLOAT/DOUBLE columns (metric_value uses DECIMAL/INTEGER)
 * - MySQL: TIMESTAMP DEFAULT CURRENT_TIMESTAMP / ON UPDATE CURRENT_TIMESTAMP
 * - MySQL: All columns have COMMENT
 * - MySQL: Indexes defined inline within CREATE TABLE (no separate CREATE INDEX
 *          to avoid ZDAS parser class cast errors)
 * - MySQL: Indexed columns use VARCHAR(255) not TEXT (OceanBase can't index TEXT)
 * - SQLite: Uses AFTER UPDATE triggers for gmt_modified auto-update
 */

export type DbType = "sqlite" | "mysql";

/**
 * Adapt a DDL statement for the target database type.
 *
 * SQLite → MySQL conversions:
 * - INTEGER PK AUTOINCREMENT → BIGINT PK AUTO_INCREMENT
 * - gmt_modified INTEGER DEFAULT (unixepoch()) → TIMESTAMP ... ON UPDATE CURRENT_TIMESTAMP
 * - gmt_create INTEGER DEFAULT (unixepoch()) → TIMESTAMP ... DEFAULT CURRENT_TIMESTAMP
 * - DECIMAL(20,6) preserved (MySQL native)
 * - Strips AFTER UPDATE triggers (MySQL handles via ON UPDATE)
 *
 * MySQL → SQLite conversions:
 * - BIGINT PK AUTO_INCREMENT → INTEGER PK AUTOINCREMENT
 * - TIMESTAMP → INTEGER DEFAULT (unixepoch())
 * - Strips ON UPDATE CURRENT_TIMESTAMP (SQLite uses triggers)
 * - DECIMAL(20,6) → INTEGER
 * - Strips COMMENT clauses
 */
export function adaptDdl(sql: string, dbType: DbType): string {
  if (dbType === "mysql") {
    // ZDAS/OceanBase parser fails on standalone CREATE INDEX (only inline indexes in CREATE TABLE).
    // MySQL migration scripts define indexes inline, so skip standalone CREATE INDEX here.
    if (/^\s*CREATE\s+(UNIQUE\s+)?INDEX\s/i.test(sql)) {
      return "SELECT 0 -- MySQL: standalone CREATE INDEX skipped (defined inline in migration script)";
    }
    return sql
      .replace(/\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT/gi, "BIGINT PRIMARY KEY AUTO_INCREMENT")
      .replace(/\bINT\s+PRIMARY\s+KEY\s+AUTOINCREMENT/gi, "BIGINT PRIMARY KEY AUTO_INCREMENT")
      .replace(/\bINTEGER\s+PRIMARY\s+KEY\s+AUTO_INCREMENT/gi, "BIGINT PRIMARY KEY AUTO_INCREMENT")
      .replace(/\bINT\s+PRIMARY\s+KEY\s+AUTO_INCREMENT/gi, "BIGINT PRIMARY KEY AUTO_INCREMENT")
      // BIGINT (non-PK): SQLite canonical for overflow-prone columns → MySQL BIGINT
      // This keeps the MySQL column as BIGINT (8 bytes) instead of INT (4 bytes).
      // Non-PK BIGINT in SQLite canonical DDL means "this can exceed INT range".
      .replace(/\bBIGINT\b/gi, "BIGINT")
      // gmt_modified: add ON UPDATE and TIMESTAMP type
      .replace(
        /gmt_modified\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+\(unixepoch\(\)\)/gi,
        "gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
      )
      // gmt_create: TIMESTAMP type
      .replace(
        /gmt_create\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+\(unixepoch\(\)\)/gi,
        "gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
      )
      // Remaining unixepoch() references
      .replace(/\(unixepoch\(\)\)/gi, "CURRENT_TIMESTAMP")
      .replace(/\bREAL\b/gi, "DECIMAL(20,6)")
      // Add COMMENT to id columns
      .replace(
        /\bid\s+(BIGINT\s+PRIMARY\s+KEY\s+AUTO_INCREMENT)\b/gi,
        "id $1 COMMENT '主键ID'",
      );
  }
  // SQLite: keep INTEGER DEFAULT (unixepoch()), strip MySQL-specific syntax
  return sql
    .replace(/BIGINT\s+PRIMARY\s+KEY\s+AUTO_INCREMENT/gi, "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace(/INTEGER\s+PRIMARY\s+KEY\s+AUTO_INCREMENT/gi, "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace(/INT\s+PRIMARY\s+KEY\s+AUTO_INCREMENT/gi, "INTEGER PRIMARY KEY AUTOINCREMENT")
    // SQLite does not support ALTER TABLE ... MODIFY COLUMN.
    // For INT→BIGINT migration SQL: this is a no-op in SQLite since INTEGER is
    // already unbounded. Strip the statement entirely by replacing with a
    // harmless SELECT 0 (empty transaction filler to preserve statement count).
    .replace(/ALTER\s+TABLE\s+\w+\s+MODIFY\s+COLUMN\s+[\s\S]*?$/gm, "SELECT 0 -- SQLite: MODIFY COLUMN skipped (INTEGER is unbounded)")
    // Non-PK BIGINT → INTEGER for SQLite (SQLite INTEGER is unbounded, same behavior)
    .replace(/\bBIGINT\b/gi, "INTEGER")
    .replace(/TIMESTAMP\s+NOT\s+NULL\s+DEFAULT\s+CURRENT_TIMESTAMP\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP/gi,
      "INTEGER NOT NULL DEFAULT (unixepoch())")
    .replace(/TIMESTAMP\s+NOT\s+NULL\s+DEFAULT\s+CURRENT_TIMESTAMP/gi,
      "INTEGER NOT NULL DEFAULT (unixepoch())")
    .replace(/CURRENT_TIMESTAMP/gi, "(unixepoch())")
    .replace(/\bCOMMENT\s+'[^']*'/gi, "")
    .replace(/DECIMAL\(\d+,\s*\d+\)/gi, "INTEGER")
    .replace(/MEDIUMTEXT/gi, "TEXT")
    .replace(/DOUBLE\s+PRECISION/gi, "REAL");
}

/**
 * Trigger DDL for auto-updating gmt_modified on SQLite.
 * MySQL handles this via ON UPDATE CURRENT_TIMESTAMP, so these
 * are only needed for SQLite.
 */
export const sqliteTriggers: string[] = [
  `CREATE TRIGGER IF NOT EXISTS trg_flow_events_update AFTER UPDATE ON flow_events FOR EACH ROW BEGIN UPDATE flow_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_metrics_update AFTER UPDATE ON flow_metrics FOR EACH ROW BEGIN UPDATE flow_metrics SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_triggered_alerts_update AFTER UPDATE ON triggered_alerts FOR EACH ROW BEGIN UPDATE triggered_alerts SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_node_executions_update AFTER UPDATE ON node_executions FOR EACH ROW BEGIN UPDATE node_executions SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_runs_update AFTER UPDATE ON flow_runs FOR EACH ROW BEGIN UPDATE flow_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_scheduled_triggers_update AFTER UPDATE ON scheduled_triggers FOR EACH ROW BEGIN UPDATE scheduled_triggers SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_webhook_triggers_update AFTER UPDATE ON webhook_triggers FOR EACH ROW BEGIN UPDATE webhook_triggers SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_webhook_events_update AFTER UPDATE ON webhook_events FOR EACH ROW BEGIN UPDATE webhook_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_schema_version_update AFTER UPDATE ON schema_version FOR EACH ROW BEGIN UPDATE schema_version SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_validation_templates_update AFTER UPDATE ON validation_templates FOR EACH ROW BEGIN UPDATE validation_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_facade_bindings_update AFTER UPDATE ON facade_bindings FOR EACH ROW BEGIN UPDATE facade_bindings SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_workflow_specs_update AFTER UPDATE ON workflow_specs FOR EACH ROW BEGIN UPDATE workflow_specs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_node_step_traces_update AFTER UPDATE ON node_step_traces FOR EACH ROW BEGIN UPDATE node_step_traces SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_execution_step_log_update AFTER UPDATE ON execution_step_log FOR EACH ROW BEGIN UPDATE execution_step_log SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_run_logs_update AFTER UPDATE ON run_logs FOR EACH ROW BEGIN UPDATE run_logs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  ];

/**
 * All migration definitions, in order.
 * Each migration uses SQLite-compatible SQL as the canonical form.
 * For MySQL: adaptDdl converts types and the init_mysql.sql file
 * contains the fully-compliant MySQL DDL with COMMENTs and inline indexes.
 * For SQLite: triggers for gmt_modified auto-update are in sqliteTriggers.
 *
 * Note: Indexed columns use VARCHAR(255) instead of TEXT because
 * OceanBase/MySQL cannot index TEXT columns. SQLite treats VARCHAR
 * identically to TEXT so this is fully compatible.
 */
export const migrations: ReadonlyArray<{ version: number; description: string; sql: string[]; sqliteOnly?: boolean; mysqlOnly?: boolean }> = [
  {
    version: 1,
    description: "Initial schema: flow_events, flow_metrics, triggered_alerts",
    sql: [
      `CREATE TABLE IF NOT EXISTS flow_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  event_type VARCHAR(255) NOT NULL,
  attempt INTEGER,
  time INTEGER NOT NULL,
  data_json TEXT,
  error_text TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_events_flow_id ON flow_events (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_events_workflow_id ON flow_events (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_events_time ON flow_events (time)`,

      `CREATE TABLE IF NOT EXISTS flow_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  metric_name VARCHAR(255) NOT NULL,
  metric_value DECIMAL(20,6) NOT NULL,
  time INTEGER NOT NULL,
  labels_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_metrics_workflow ON flow_metrics (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_metrics_name ON flow_metrics (metric_name)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_metrics_time ON flow_metrics (time)`,

      `CREATE TABLE IF NOT EXISTS triggered_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  alert_rule VARCHAR(255) NOT NULL,
  severity VARCHAR(255) NOT NULL DEFAULT 'warning',
  message TEXT NOT NULL,
  time INTEGER NOT NULL,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_triggered_alerts_workflow ON triggered_alerts (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_triggered_alerts_ack ON triggered_alerts (acknowledged)`,
      `CREATE INDEX IF NOT EXISTS idx_triggered_alerts_time ON triggered_alerts (time)`,
    ],
  },
  {
    version: 2,
    description: "Add node_executions and flow_runs tables for state persistence and query API",
    sql: [
      `CREATE TABLE IF NOT EXISTS node_executions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  executor_type VARCHAR(255),
  status VARCHAR(255) NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  input_json TEXT,
  output_json TEXT,
  error_text TEXT,
  duration_ms BIGINT,
  token_usage_json TEXT,
  started_at BIGINT NOT NULL,
  completed_at BIGINT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_node_exec_flow_id ON node_executions (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_node_exec_workflow_id ON node_executions (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_node_exec_node_status ON node_executions (flow_id, node_id, status)`,
      `CREATE INDEX IF NOT EXISTS idx_node_exec_created ON node_executions (gmt_create)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_node_exec_flow_node_attempt ON node_executions (flow_id, node_id, attempt)`,

      `CREATE TABLE IF NOT EXISTS flow_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  workflow_title VARCHAR(255),
  status VARCHAR(255) NOT NULL,
  params_json TEXT,
  input_json TEXT,
  result_json TEXT,
  node_count INTEGER NOT NULL DEFAULT 0,
  succeeded_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  total_duration_ms BIGINT,
  total_token_usage BIGINT,
  started_at BIGINT NOT NULL,
  completed_at BIGINT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_flow_runs_flow_id ON flow_runs (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_workflow_id ON flow_runs (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_status ON flow_runs (status)`,
      `CREATE INDEX IF NOT EXISTS idx_flow_runs_started ON flow_runs (started_at)`,
    ],
  },
  {
    version: 3,
    description: "Add scheduled_triggers table for cron scheduler",
    sql: [
      `CREATE TABLE IF NOT EXISTS scheduled_triggers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255) NOT NULL,
  cron_expression VARCHAR(255) NOT NULL,
  timezone VARCHAR(255) NOT NULL DEFAULT 'UTC',
  params_json TEXT,
  max_concurrent INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_fire_time INTEGER,
  next_fire_time INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_sched_triggers_workflow ON scheduled_triggers (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_sched_triggers_enabled_next ON scheduled_triggers (enabled, next_fire_time)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_sched_triggers_trigger_id ON scheduled_triggers (trigger_id)`,
    ],
  },
  {
    version: 4,
    description: "Add webhook_triggers and webhook_events tables for webhook trigger system",
    sql: [
      `CREATE TABLE IF NOT EXISTS webhook_triggers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  secret VARCHAR(255),
  payload_mapping TEXT,
  allowed_ips TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_webhook_triggers_workflow ON webhook_triggers (workflow_id)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_webhook_triggers_trigger_id ON webhook_triggers (trigger_id)`,

      `CREATE TABLE IF NOT EXISTS webhook_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id VARCHAR(255) NOT NULL,
  trigger_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255),
  status VARCHAR(255) NOT NULL,
  request_method VARCHAR(255) NOT NULL,
  request_headers TEXT,
  request_body_hash VARCHAR(255),
  response_code INTEGER,
  error_message TEXT,
  ip_address VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_webhook_events_trigger ON webhook_events (trigger_id)`,
      `CREATE INDEX IF NOT EXISTS idx_webhook_events_event_id ON webhook_events (event_id)`,
      `CREATE INDEX IF NOT EXISTS idx_webhook_events_created ON webhook_events (gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_webhook_events_dedup ON webhook_events (event_id, gmt_create)`,
    ],
  },
  {
    version: 5,
    description: "Add validation_templates table for LLM-based output validation",
    sql: [
      `CREATE TABLE IF NOT EXISTS validation_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  content TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_validation_templates_template_id ON validation_templates (template_id)`,
    ],
  },
  {
    version: 6,
    description: "Add facade_bindings table for command-to-workflow facade binding",
    sql: [
      `CREATE TABLE IF NOT EXISTS facade_bindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  remark TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_facade_bindings_command ON facade_bindings (command)`,
      `CREATE INDEX IF NOT EXISTS idx_facade_bindings_workflow ON facade_bindings (workflow_id)`,
    ],
  },
  {
    version: 7,
    description: "Add triggered_by, identity_key, current_phase to flow_runs; add node_title, progress_message to node_executions",
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN triggered_by VARCHAR(255)`,
      `ALTER TABLE flow_runs ADD COLUMN identity_key TEXT`,
      `ALTER TABLE flow_runs ADD COLUMN current_phase VARCHAR(255)`,
      `ALTER TABLE node_executions ADD COLUMN node_title VARCHAR(255)`,
      `ALTER TABLE node_executions ADD COLUMN progress_message TEXT`,
    ],
  },
  {
    version: 8,
    description: "Add workflow_specs table for DB-persisted workflow configurations",
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_specs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  spec_json MEDIUMTEXT NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_workflow_specs_workflow_id ON workflow_specs (workflow_id)`,
    ],
  },
  {
    version: 9,
    description: "Add session_key and session_id to node_executions",
    sql: [
      `ALTER TABLE node_executions ADD COLUMN session_key VARCHAR(255)`,
      `ALTER TABLE node_executions ADD COLUMN session_id VARCHAR(255)`,
    ],
  },
  {
    version: 10,
    description: "Add approval_cards table for card-web delivery",
    sql: [
      `CREATE TABLE IF NOT EXISTS approval_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  workflow_title VARCHAR(255),
  approval_type VARCHAR(255),
  message TEXT,
  card_fields_json TEXT,
  approver_ids TEXT NOT NULL,
  approver_names TEXT,
  approval_policy VARCHAR(50) NOT NULL DEFAULT 'any',
  approved_by VARCHAR(4000) NOT NULL DEFAULT '',
  rejected_by VARCHAR(4000) NOT NULL DEFAULT '',
  status VARCHAR(50) NOT NULL DEFAULT 'pending',
  delivery_mode VARCHAR(50) NOT NULL DEFAULT 'card-web',
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_approval_cards_flow_node ON approval_cards (flow_id, node_id)`,
      `CREATE INDEX IF NOT EXISTS idx_approval_cards_status ON approval_cards (status)`,
      `CREATE INDEX IF NOT EXISTS idx_approval_cards_created ON approval_cards (created_at)`,
      `CREATE TRIGGER IF NOT EXISTS trg_approval_cards_update
AFTER UPDATE ON approval_cards FOR EACH ROW
BEGIN
  UPDATE approval_cards SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
    ],
  },
  {
    version: 11,
    description: "Add system_context_json to node_executions for recording trigger evaluation, hook outcomes, retry context, executor details, and human actions",
    sql: [
      `ALTER TABLE node_executions ADD COLUMN system_context_json TEXT`,
    ],
  },
  {
    version: 12,
    description: "Add flow_control_slots and flow_control_queue tables for flow control",
    sql: [
      `CREATE TABLE IF NOT EXISTS flow_control_slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id VARCHAR(255) NOT NULL,
  scope_key VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  acquired_at INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_fc_slots_instance_scope_flow_node ON flow_control_slots (instance_id, scope_key, flow_id, node_id)`,
      `CREATE INDEX IF NOT EXISTS idx_fc_slots_instance_scope ON flow_control_slots (instance_id, scope_key)`,
      `CREATE TRIGGER IF NOT EXISTS trg_flow_control_slots_update
AFTER UPDATE ON flow_control_slots FOR EACH ROW
BEGIN
  UPDATE flow_control_slots SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
      `CREATE TABLE IF NOT EXISTS flow_control_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id VARCHAR(255) NOT NULL,
  scope_key VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  priority INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  enqueued_at INTEGER NOT NULL DEFAULT (unixepoch()),
  dispatch_after INTEGER,
  expires_at INTEGER,
  payload TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_fc_queue_instance_scope_status ON flow_control_queue (instance_id, scope_key, status, priority, enqueued_at)`,
      `CREATE INDEX IF NOT EXISTS idx_fc_queue_expires ON flow_control_queue (expires_at, status)`,
      `CREATE INDEX IF NOT EXISTS idx_fc_queue_instance_flow ON flow_control_queue (instance_id, flow_id)`,
      `CREATE TRIGGER IF NOT EXISTS trg_flow_control_queue_update
AFTER UPDATE ON flow_control_queue FOR EACH ROW
BEGIN
  UPDATE flow_control_queue SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
    ],
  },
  {
    version: 13,
    description: "Add session_id to flow_control_slots for session-liveness-based zombie detection",
    sql: [
      `ALTER TABLE flow_control_slots ADD COLUMN session_id VARCHAR(255)`,
      `CREATE INDEX IF NOT EXISTS idx_fc_slots_session_id ON flow_control_slots (session_id)`,
    ],
  },
  {
    version: 14,
    description: "Add lease-based flow control columns (lease_expires_at, renew_count) to replace zombie detection with heartbeat",
    sql: [
      `ALTER TABLE flow_control_slots ADD COLUMN lease_expires_at INTEGER NOT NULL DEFAULT 0`,
      `ALTER TABLE flow_control_slots ADD COLUMN renew_count INTEGER NOT NULL DEFAULT 0`,
      `CREATE INDEX IF NOT EXISTS idx_fc_slots_lease_expiry ON flow_control_slots (instance_id, lease_expires_at)`,
    ],
  },
  {
    version: 15,
    description: "Add node_step_traces table for recording embedded-agent execution steps (tool_call / tool_result / assistant_text)",
    sql: [
      `CREATE TABLE IF NOT EXISTS node_step_traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  step_seq INTEGER NOT NULL,
  step_type VARCHAR(32) NOT NULL,
  skill_name VARCHAR(255),
  tool_name VARCHAR(255),
  tool_use_id VARCHAR(255),
  tool_input_json TEXT,
  tool_output_text TEXT,
  is_error INTEGER NOT NULL DEFAULT 0,
  text_content TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_nst_flow_node ON node_step_traces (flow_id, node_id, attempt)`,
      `CREATE INDEX IF NOT EXISTS idx_nst_flow_id ON node_step_traces (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_nst_skill_name ON node_step_traces (skill_name)`,
      `CREATE INDEX IF NOT EXISTS idx_nst_created ON node_step_traces (gmt_create)`,
      `CREATE TRIGGER IF NOT EXISTS trg_node_step_traces_update
AFTER UPDATE ON node_step_traces FOR EACH ROW
BEGIN
  UPDATE node_step_traces SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
    ],
  },
  {
    version: 16,
    description: "Add node_hallucination_checks table for rule-based hallucination detection on embedded-agent execution steps",
    sql: [
      `CREATE TABLE IF NOT EXISTS node_hallucination_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  check_type VARCHAR(32) NOT NULL,
  severity VARCHAR(16) NOT NULL DEFAULT 'low',
  passed INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  evidence TEXT,
  risk_score INTEGER NOT NULL DEFAULT 0,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'none',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_nhc_flow_node ON node_hallucination_checks (flow_id, node_id, attempt)`,
      `CREATE TRIGGER IF NOT EXISTS trg_node_hallucination_checks_update
AFTER UPDATE ON node_hallucination_checks FOR EACH ROW
BEGIN
  UPDATE node_hallucination_checks SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
    ],
  },
  {
    version: 17,
    description: "Add embedded_session_key to node_executions for Langfuse trace correlation",
    sql: [
      `ALTER TABLE node_executions ADD COLUMN embedded_session_key VARCHAR(512)`,
    ],
  },
  {
    version: 18,
    description: "Add Langfuse correlation and observability fields to node_step_traces",
    sql: [
      `ALTER TABLE node_step_traces ADD COLUMN session_key VARCHAR(512)`,
      `ALTER TABLE node_step_traces ADD COLUMN trace_id VARCHAR(64)`,
      `ALTER TABLE node_step_traces ADD COLUMN observation_id VARCHAR(64)`,
      `ALTER TABLE node_step_traces ADD COLUMN model VARCHAR(255)`,
      `ALTER TABLE node_step_traces ADD COLUMN latency_ms INTEGER`,
      `ALTER TABLE node_step_traces ADD COLUMN prompt_tokens INTEGER`,
      `ALTER TABLE node_step_traces ADD COLUMN completion_tokens INTEGER`,
    ],
  },
  {
    version: 19,
    description: "Add origin_session_key and origin_session_id to flow_runs for traceability",
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN origin_session_key VARCHAR(512)`,
      `ALTER TABLE flow_runs ADD COLUMN origin_session_id VARCHAR(255)`,
    ],
  },
  {
    version: 20,
    description: "Add callback_tokens table for async-callback node support",
    sql: [
      `CREATE TABLE IF NOT EXISTS callback_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token VARCHAR(36) NOT NULL UNIQUE,
        flow_id VARCHAR(128) NOT NULL,
        node_id VARCHAR(128) NOT NULL,
        workflow_id VARCHAR(128),
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        callback_result TEXT,
        callback_headers TEXT,
        callback_ip VARCHAR(45),
        callback_user_id VARCHAR(128),
        timeout_at INTEGER,
        created_at INTEGER NOT NULL DEFAULT (unixepoch()),
        consumed_at INTEGER,
        expired_at INTEGER,
        gmt_modified INTEGER
      )`,
      `CREATE INDEX IF NOT EXISTS idx_ct_token ON callback_tokens (token)`,
      `CREATE INDEX IF NOT EXISTS idx_ct_flow_node ON callback_tokens (flow_id, node_id)`,
      `CREATE INDEX IF NOT EXISTS idx_ct_status_timeout ON callback_tokens (status, timeout_at)`,
      `CREATE TRIGGER IF NOT EXISTS trg_callback_tokens_update
AFTER UPDATE ON callback_tokens FOR EACH ROW
BEGIN
  UPDATE callback_tokens SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
    ],
  },
  {
    version: 21,
    description: "Add execution_step_log table for dynamic workflow execution observability",
    sql: [
      `CREATE TABLE IF NOT EXISTS execution_step_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  step_type VARCHAR(32) NOT NULL,
  timestamp BIGINT NOT NULL,
  input_summary TEXT,
  output_summary TEXT,
  llm_evaluation TEXT,
  decision_path TEXT,
  duration_ms BIGINT,
  token_usage BIGINT,
  metadata TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_esl_flow_id ON execution_step_log (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_esl_flow_node ON execution_step_log (flow_id, node_id)`,
      `CREATE INDEX IF NOT EXISTS idx_esl_step_type ON execution_step_log (flow_id, step_type)`,
      `CREATE TRIGGER IF NOT EXISTS trg_execution_step_log_update
AFTER UPDATE ON execution_step_log FOR EACH ROW
BEGIN
  UPDATE execution_step_log SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END`,
    ],
  },
  {
    version: 22,
    description: "Fix INT overflow: change overflow-prone INTEGER columns to BIGINT for MySQL/OceanBase compatibility (MySQL INT max 2,147,483,647; flow_runs.total_duration_ms can exceed this for long-running zombie flows)",
    sql: [
      // flow_runs: total_duration_ms is the primary overflow victim
      // (ranForSecs * 1000 can exceed INT max when flow runs > ~24.8 days)
      `ALTER TABLE flow_runs MODIFY COLUMN total_duration_ms BIGINT`,
      `ALTER TABLE flow_runs MODIFY COLUMN total_token_usage BIGINT`,
      `ALTER TABLE flow_runs MODIFY COLUMN started_at BIGINT`,
      `ALTER TABLE flow_runs MODIFY COLUMN completed_at BIGINT`,
      // node_executions: duration_ms and timestamps same risk
      `ALTER TABLE node_executions MODIFY COLUMN duration_ms BIGINT`,
      `ALTER TABLE node_executions MODIFY COLUMN started_at BIGINT`,
      `ALTER TABLE node_executions MODIFY COLUMN completed_at BIGINT`,
      // execution_step_log: duration and token counters
      `ALTER TABLE execution_step_log MODIFY COLUMN duration_ms BIGINT`,
      `ALTER TABLE execution_step_log MODIFY COLUMN token_usage BIGINT`,
      `ALTER TABLE execution_step_log MODIFY COLUMN timestamp BIGINT`,
    ],
  },
  {
    version: 24,
    description: "Add plugin_version to flow_runs for engine version tracking",
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN plugin_version VARCHAR(255) DEFAULT NULL`,
    ],
  },
  {
    version: 25,
    description: "Add http_callback_configs table for HTTP callback notification system",
    sql: [
      `CREATE TABLE IF NOT EXISTS http_callback_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  url VARCHAR(1024) NOT NULL,
  secret VARCHAR(1024),
  enabled INTEGER NOT NULL DEFAULT 1,
  notify_on TEXT NOT NULL,
  timeout_ms INTEGER NOT NULL DEFAULT 5000,
  max_retries INTEGER NOT NULL DEFAULT 2,
  retry_delay_ms INTEGER NOT NULL DEFAULT 1000,
  include_node_output INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (config_id)
)`,
      // ZDAS/OceanBase requires indexes inline in CREATE TABLE (no separate CREATE INDEX).
      // The MySQL migration script (migrate_v25_mysql.sql) defines indexes inline with KEY syntax.
      // This separate CREATE INDEX is SQLite-only — adaptDdl does not rewrite it for MySQL.
      `CREATE INDEX IF NOT EXISTS idx_http_callback_configs_workflow ON http_callback_configs (workflow_id)`,
    ],
  },
  {
    version: 26,
    description: "Make http_callback_configs.secret nullable — MySQL",
    mysqlOnly: true,
    sql: [
      `ALTER TABLE http_callback_configs MODIFY COLUMN secret VARCHAR(1024) NULL COMMENT 'HMAC-SHA256 signing secret (optional)'`,
    ],
  },
  {
    version: 27,
    description: "Make http_callback_configs.secret nullable — SQLite",
    sqliteOnly: true,
    sql: [
      `ALTER TABLE http_callback_configs RENAME TO http_callback_configs_old`,
      `CREATE TABLE http_callback_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  url VARCHAR(1024) NOT NULL,
  secret VARCHAR(1024),
  enabled INTEGER NOT NULL DEFAULT 1,
  notify_on TEXT NOT NULL,
  timeout_ms INTEGER NOT NULL DEFAULT 5000,
  max_retries INTEGER NOT NULL DEFAULT 2,
  retry_delay_ms INTEGER NOT NULL DEFAULT 1000,
  include_node_output INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (config_id)
)`,
      `INSERT INTO http_callback_configs SELECT * FROM http_callback_configs_old`,
      `DROP TABLE http_callback_configs_old`,
      `CREATE INDEX IF NOT EXISTS idx_http_callback_configs_workflow ON http_callback_configs (workflow_id)`,
    ],
  },
  {
    version: 28,
    description: "Add http_callback_logs table for HTTP callback audit logging",
    sql: [
      `CREATE TABLE IF NOT EXISTS http_callback_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  config_id VARCHAR(255) NOT NULL,
  config_name VARCHAR(255),
  callback_url VARCHAR(1024) NOT NULL,
  notify_event VARCHAR(64) NOT NULL,
  node_id VARCHAR(255),
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  request_body TEXT,
  request_headers TEXT,
  response_status_code INTEGER,
  response_body TEXT,
  duration_ms INTEGER,
  status VARCHAR(32) NOT NULL DEFAULT 'sent',
  error_message TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_http_callback_logs_flow ON http_callback_logs (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_http_callback_logs_workflow ON http_callback_logs (workflow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_http_callback_logs_status ON http_callback_logs (status)`,
      `CREATE INDEX IF NOT EXISTS idx_http_callback_logs_config ON http_callback_logs (config_id)`,
    ],
  },
  {
    version: 29,
    description: "Add resolved_prompt column to node_executions for storing template-resolved prompt text",
    sql: [
      `ALTER TABLE node_executions ADD COLUMN resolved_prompt TEXT`,
    ],
  },
  {
    version: 30,
    description: "Add engine column to flow_runs for tracking which host platform started the flow",
    sql: [
      `ALTER TABLE flow_runs ADD COLUMN engine VARCHAR(255) DEFAULT NULL`,
    ],
  },
  {
    version: 31,
    description: "Add unique index on node_executions (flow_id, node_id, attempt) to prevent duplicate rows on manual retry",
    sql: [
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_node_exec_flow_node_attempt ON node_executions (flow_id, node_id, attempt)`,
    ],
  },
  {
    version: 32,
    description: "MySQL: add unique index on node_executions (flow_id, node_id, attempt) — inline DDL for ZDAS/OceanBase compatibility",
    sql: [
      `ALTER TABLE node_executions ADD UNIQUE INDEX uk_node_exec_flow_node_attempt (flow_id, node_id, attempt)`,
    ],
    mysqlOnly: true,
  },
  {
    version: 33,
    description: "Add cm_app_config table for DB-stored application configuration",
    sql: [
      `CREATE TABLE IF NOT EXISTS cm_app_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_key VARCHAR(64) NOT NULL,
  config_yaml TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  updated_by VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_app_config_key ON cm_app_config (config_key)`,
      `CREATE INDEX IF NOT EXISTS idx_cm_app_config_enabled ON cm_app_config (enabled)`,
    ],
  },
  {
    version: 34,
    description: "Create run_logs table for console log capture",
    sql: [
      `CREATE TABLE IF NOT EXISTS run_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  level VARCHAR(32) NOT NULL,
  source VARCHAR(255),
  message TEXT NOT NULL,
  timestamp BIGINT NOT NULL,
  seq INTEGER NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_run_logs_flow_id ON run_logs (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_logs_flow_node ON run_logs (flow_id, node_id)`,
      `CREATE INDEX IF NOT EXISTS idx_run_logs_level ON run_logs (flow_id, level)`,
    ],
  },
  {
    version: 35,
    description: "Create campaign tables for cross-execution aggregation",
    sql: [
      `CREATE TABLE IF NOT EXISTS campaigns (
  id VARCHAR(255) PRIMARY KEY,
  goal TEXT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  budget_max_tokens INTEGER,
  budget_max_flows INTEGER,
  budget_max_iterations INTEGER,
  used_tokens INTEGER NOT NULL DEFAULT 0,
  used_iterations INTEGER NOT NULL DEFAULT 0,
  flow_count INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns (status)`,

      `CREATE TABLE IF NOT EXISTS campaign_flows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  token_usage INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER NOT NULL DEFAULT (unixepoch()),
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_campaign_flows_flow ON campaign_flows (flow_id)`,
      `CREATE INDEX IF NOT EXISTS idx_campaign_flows_campaign ON campaign_flows (campaign_id)`,

      `CREATE TABLE IF NOT EXISTS campaign_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  summary TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_campaign_ev_campaign ON campaign_evidence (campaign_id)`,
      `CREATE INDEX IF NOT EXISTS idx_campaign_ev_flow ON campaign_evidence (flow_id)`,

      `CREATE TABLE IF NOT EXISTS campaign_gates (
  id VARCHAR(255) PRIMARY KEY,
  campaign_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  prompt TEXT NOT NULL,
  options_json TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  reason TEXT,
  resolved_by VARCHAR(255),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE INDEX IF NOT EXISTS idx_campaign_gates_campaign ON campaign_gates (campaign_id)`,
      `CREATE INDEX IF NOT EXISTS idx_campaign_gates_status ON campaign_gates (status)`,
    ],
  },
];
