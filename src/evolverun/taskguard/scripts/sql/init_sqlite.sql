-- ClawFlow Schema DDL — SQLite
-- Aligned with src/db/schema.ts migrations v1–v4 and ClawWeb server/schema.ts migrations v5–v7
-- Compliance: id INTEGER AUTOINCREMENT, gmt_create/gmt_modified required, no FLOAT/DOUBLE
-- Note: SQLite does not support COMMENT or ON UPDATE CURRENT_TIMESTAMP.
--       gmt_modified is updated via triggers (defined below each table).
--       Indexed columns use VARCHAR(255) for MySQL compatibility (SQLite treats same as TEXT).
-- Usage: sqlite3 engine.db < init_sqlite.sql

-- ── Migration v1: flow_events, flow_metrics, triggered_alerts ──

CREATE TABLE IF NOT EXISTS flow_events (
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
);

CREATE INDEX IF NOT EXISTS idx_flow_events_flow_id ON flow_events (flow_id);
CREATE INDEX IF NOT EXISTS idx_flow_events_workflow_id ON flow_events (workflow_id);
CREATE INDEX IF NOT EXISTS idx_flow_events_time ON flow_events (time);

CREATE TRIGGER IF NOT EXISTS trg_flow_events_update
AFTER UPDATE ON flow_events FOR EACH ROW
BEGIN
  UPDATE flow_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS flow_metrics (
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
);

CREATE INDEX IF NOT EXISTS idx_flow_metrics_workflow ON flow_metrics (workflow_id);
CREATE INDEX IF NOT EXISTS idx_flow_metrics_name ON flow_metrics (metric_name);
CREATE INDEX IF NOT EXISTS idx_flow_metrics_time ON flow_metrics (time);

CREATE TRIGGER IF NOT EXISTS trg_flow_metrics_update
AFTER UPDATE ON flow_metrics FOR EACH ROW
BEGIN
  UPDATE flow_metrics SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS triggered_alerts (
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
);

CREATE INDEX IF NOT EXISTS idx_triggered_alerts_workflow ON triggered_alerts (workflow_id);
CREATE INDEX IF NOT EXISTS idx_triggered_alerts_ack ON triggered_alerts (acknowledged);
CREATE INDEX IF NOT EXISTS idx_triggered_alerts_time ON triggered_alerts (time);

CREATE TRIGGER IF NOT EXISTS trg_triggered_alerts_update
AFTER UPDATE ON triggered_alerts FOR EACH ROW
BEGIN
  UPDATE triggered_alerts SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── Migration v2: node_executions, flow_runs ──

CREATE TABLE IF NOT EXISTS node_executions (
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
  duration_ms INTEGER,
  token_usage_json TEXT,
  started_at INTEGER NOT NULL,
  completed_at INTEGER,
  node_title VARCHAR(255),
  progress_message TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_node_exec_flow_id ON node_executions (flow_id);
CREATE INDEX IF NOT EXISTS idx_node_exec_workflow_id ON node_executions (workflow_id);
CREATE INDEX IF NOT EXISTS idx_node_exec_node_status ON node_executions (flow_id, node_id, status);
CREATE INDEX IF NOT EXISTS idx_node_exec_created ON node_executions (gmt_create);

CREATE TRIGGER IF NOT EXISTS trg_node_executions_update
AFTER UPDATE ON node_executions FOR EACH ROW
BEGIN
  UPDATE node_executions SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS flow_runs (
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
  total_duration_ms INTEGER,
  total_token_usage INTEGER,
  triggered_by VARCHAR(255),
  identity_key TEXT,
  current_phase VARCHAR(255),
  started_at INTEGER NOT NULL,
  completed_at INTEGER,
  credentials_json TEXT,
  origin_session_key VARCHAR(255),
  origin_session_id VARCHAR(255),
  origin_bot_id VARCHAR(255),
  user_id VARCHAR(255),
  plugin_version VARCHAR(255) DEFAULT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_flow_runs_flow_id ON flow_runs (flow_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_workflow_id ON flow_runs (workflow_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_status ON flow_runs (status);
CREATE INDEX IF NOT EXISTS idx_flow_runs_started ON flow_runs (started_at);

CREATE TRIGGER IF NOT EXISTS trg_flow_runs_update
AFTER UPDATE ON flow_runs FOR EACH ROW
BEGIN
  UPDATE flow_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── Migration v3: scheduled_triggers ──

CREATE TABLE IF NOT EXISTS scheduled_triggers (
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
);

CREATE INDEX IF NOT EXISTS idx_sched_triggers_workflow ON scheduled_triggers (workflow_id);
CREATE INDEX IF NOT EXISTS idx_sched_triggers_enabled_next ON scheduled_triggers (enabled, next_fire_time);
CREATE UNIQUE INDEX IF NOT EXISTS uk_sched_triggers_trigger_id ON scheduled_triggers (trigger_id);

CREATE TRIGGER IF NOT EXISTS trg_scheduled_triggers_update
AFTER UPDATE ON scheduled_triggers FOR EACH ROW
BEGIN
  UPDATE scheduled_triggers SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── Migration v4: webhook_triggers, webhook_events ──

CREATE TABLE IF NOT EXISTS webhook_triggers (
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
);

CREATE INDEX IF NOT EXISTS idx_webhook_triggers_workflow ON webhook_triggers (workflow_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_webhook_triggers_trigger_id ON webhook_triggers (trigger_id);

CREATE TRIGGER IF NOT EXISTS trg_webhook_triggers_update
AFTER UPDATE ON webhook_triggers FOR EACH ROW
BEGIN
  UPDATE webhook_triggers SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS webhook_events (
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
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_trigger ON webhook_events (trigger_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_event_id ON webhook_events (event_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_created ON webhook_events (gmt_create);
CREATE INDEX IF NOT EXISTS idx_webhook_events_dedup ON webhook_events (event_id, gmt_create);

CREATE TRIGGER IF NOT EXISTS trg_webhook_events_update
AFTER UPDATE ON webhook_events FOR EACH ROW
BEGIN
  UPDATE webhook_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── Migration v5: workflow_specs (ClawWeb) ──

CREATE TABLE IF NOT EXISTS workflow_specs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  spec_json MEDIUMTEXT NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_workflow_specs_workflow_id ON workflow_specs (workflow_id);

CREATE TRIGGER IF NOT EXISTS trg_workflow_specs_update
AFTER UPDATE ON workflow_specs FOR EACH ROW
BEGIN
  UPDATE workflow_specs SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── schema_version (migration tracker) ──

CREATE TABLE IF NOT EXISTS schema_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER NOT NULL,
  description TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TRIGGER IF NOT EXISTS trg_schema_version_update
AFTER UPDATE ON schema_version FOR EACH ROW
BEGIN
  UPDATE schema_version SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── Seed migration versions ──

INSERT OR IGNORE INTO schema_version (version, description) VALUES
  (1, 'Initial schema: flow_events, flow_metrics, triggered_alerts'),
  (2, 'Add node_executions and flow_runs tables for state persistence and query API'),
  (3, 'Add scheduled_triggers table for cron scheduler'),
  (4, 'Add webhook_triggers and webhook_events tables for webhook trigger system'),
  (5, 'Add workflow_specs table for ClawWeb browser-persisted edits'),
  (6, 'Add triggered_by to flow_runs, node_title and triggered_by to node_executions'),
  (7, 'Add identity_key and current_phase to flow_runs, progress_message to node_executions');