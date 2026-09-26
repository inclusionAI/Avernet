-- ClawWeb/ClawFlow unified schema DDL — SQLITE
-- Generated from PROD database (agentclawdb) schema via odc-cli.
-- Compliance: BIGINT id, gmt_create/gmt_modified TIMESTAMP, no FLOAT/DOUBLE,
--             inline indexes for ZDAS compatibility, utf8mb4 charset.


-- Table: schema_version
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

-- Table: flow_events
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

-- Table: flow_metrics
CREATE TABLE IF NOT EXISTS flow_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  metric_name VARCHAR(255) NOT NULL,
  metric_value INTEGER NOT NULL,
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

-- Table: triggered_alerts
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

-- Table: node_executions
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
  started_at INTEGER,
  completed_at INTEGER,
  triggered_by VARCHAR(255),
  node_title VARCHAR(255),
  branch_id VARCHAR(255),
  progress_message TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  session_key VARCHAR(255),
  session_id VARCHAR(255),
  system_context_json TEXT,
  embedded_session_key VARCHAR(255),
  resolved_prompt TEXT,
  version INTEGER DEFAULT 1
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

-- Table: flow_runs
CREATE TABLE IF NOT EXISTS flow_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  workflow_title VARCHAR(255),
  status VARCHAR(255) NOT NULL,
  params_json TEXT,
  input_json TEXT,
  result_json TEXT,
  node_count INTEGER DEFAULT 0,
  succeeded_count INTEGER DEFAULT 0,
  failed_count INTEGER DEFAULT 0,
  total_duration_ms INTEGER,
  total_token_usage INTEGER,
  triggered_by VARCHAR(255),
  identity_key TEXT,
  current_phase VARCHAR(255),
  started_at INTEGER,
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  credentials_json TEXT,
  origin_session_key VARCHAR(255),
  origin_session_id VARCHAR(255),
  origin_bot_id VARCHAR(255),
  user_id VARCHAR(255),
  plugin_version VARCHAR(255),
  engine VARCHAR(255)
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_flow_runs_flow_id ON flow_runs (flow_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_workflow_id ON flow_runs (workflow_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_status ON flow_runs (status);
CREATE INDEX IF NOT EXISTS idx_flow_runs_started ON flow_runs (started_at);
CREATE INDEX IF NOT EXISTS idx_flow_runs_user_id ON flow_runs (user_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_status_started ON flow_runs (status, started_at);
CREATE TRIGGER IF NOT EXISTS trg_flow_runs_update
AFTER UPDATE ON flow_runs FOR EACH ROW
BEGIN
  UPDATE flow_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: scheduled_triggers
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

-- Table: webhook_triggers
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

-- Table: webhook_events
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
  event_type VARCHAR(255),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  payload_json TEXT,
  received_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch())
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

-- Table: workflow_specs
CREATE TABLE IF NOT EXISTS workflow_specs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  spec_json TEXT NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  title VARCHAR(255),
  version VARCHAR(255)
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_workflow_specs_workflow_id ON workflow_specs (workflow_id);
CREATE INDEX IF NOT EXISTS idx_wfs_workflow_version ON workflow_specs (workflow_id, version);
CREATE TRIGGER IF NOT EXISTS trg_workflow_specs_update
AFTER UPDATE ON workflow_specs FOR EACH ROW
BEGIN
  UPDATE workflow_specs SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: knowledge_bases
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  instance_name VARCHAR(255) NOT NULL,
  interface_name VARCHAR(255) NOT NULL,
  token VARCHAR(255) NOT NULL,
  user_name VARCHAR(255) NOT NULL,
  user_id VARCHAR(255) NOT NULL,
  top_k INTEGER NOT NULL DEFAULT 3,
  ranking_threshold INTEGER NOT NULL DEFAULT '0.01',
  vector_threshold INTEGER NOT NULL DEFAULT '0.6',
  ranking_model VARCHAR(255) NOT NULL DEFAULT 'bge-reranker-base',
  env VARCHAR(255) NOT NULL DEFAULT 'prod',
  enabled INTEGER NOT NULL DEFAULT 1,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_knowledge_bases_kb_id ON knowledge_bases (kb_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_enabled ON knowledge_bases (enabled);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_created ON knowledge_bases (gmt_create);
CREATE TRIGGER IF NOT EXISTS trg_knowledge_bases_update
AFTER UPDATE ON knowledge_bases FOR EACH ROW
BEGIN
  UPDATE knowledge_bases SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: validation_templates
CREATE TABLE IF NOT EXISTS validation_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  content TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  category VARCHAR(255),
  grading_type VARCHAR(255),
  timeout_seconds INTEGER,
  grading_weights_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_validation_templates_template_id ON validation_templates (template_id);
CREATE TRIGGER IF NOT EXISTS trg_validation_templates_update
AFTER UPDATE ON validation_templates FOR EACH ROW
BEGIN
  UPDATE validation_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: facade_bindings
CREATE TABLE IF NOT EXISTS facade_bindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  remark TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_facade_bindings_command ON facade_bindings (command);
CREATE INDEX IF NOT EXISTS idx_facade_bindings_workflow ON facade_bindings (workflow_id);
CREATE TRIGGER IF NOT EXISTS trg_facade_bindings_update
AFTER UPDATE ON facade_bindings FOR EACH ROW
BEGIN
  UPDATE facade_bindings SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: approval_cards
CREATE TABLE IF NOT EXISTS approval_cards (
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
  approval_policy VARCHAR(255) NOT NULL DEFAULT 'any',
  approved_by VARCHAR(255) NOT NULL,
  rejected_by VARCHAR(255) NOT NULL,
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  delivery_mode VARCHAR(255) NOT NULL DEFAULT 'card-web',
  created_at INTEGER NOT NULL,
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  comment TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_cards_flow_node ON approval_cards (flow_id, node_id);
CREATE INDEX IF NOT EXISTS idx_approval_cards_status ON approval_cards (status);
CREATE INDEX IF NOT EXISTS idx_approval_cards_created ON approval_cards (created_at);
CREATE TRIGGER IF NOT EXISTS trg_approval_cards_update
AFTER UPDATE ON approval_cards FOR EACH ROW
BEGIN
  UPDATE approval_cards SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: node_step_traces
CREATE TABLE IF NOT EXISTS node_step_traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  step_seq INTEGER NOT NULL,
  step_type VARCHAR(255) NOT NULL,
  skill_name VARCHAR(255),
  tool_name VARCHAR(255),
  tool_use_id VARCHAR(255),
  tool_input_json TEXT,
  tool_output_text TEXT,
  is_error INTEGER NOT NULL DEFAULT 0,
  text_content TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  session_key VARCHAR(255),
  trace_id VARCHAR(255),
  observation_id VARCHAR(255),
  model VARCHAR(255),
  latency_ms INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nst_flow_node ON node_step_traces (flow_id, node_id, attempt);
CREATE INDEX IF NOT EXISTS idx_nst_flow_id ON node_step_traces (flow_id);
CREATE INDEX IF NOT EXISTS idx_nst_skill_name ON node_step_traces (skill_name);
CREATE INDEX IF NOT EXISTS idx_nst_created ON node_step_traces (gmt_create);
CREATE TRIGGER IF NOT EXISTS trg_node_step_traces_update
AFTER UPDATE ON node_step_traces FOR EACH ROW
BEGIN
  UPDATE node_step_traces SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: flow_control_slots
CREATE TABLE IF NOT EXISTS flow_control_slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id VARCHAR(255) NOT NULL,
  scope_key VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  acquired_at INTEGER NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  session_id VARCHAR(255),
  lease_expires_at INTEGER NOT NULL DEFAULT 0,
  renew_count INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_fc_slots_instance_scope_flow_node ON flow_control_slots (instance_id, scope_key, flow_id, node_id);
CREATE INDEX IF NOT EXISTS idx_fc_slots_instance_scope ON flow_control_slots (instance_id, scope_key);
CREATE TRIGGER IF NOT EXISTS trg_flow_control_slots_update
AFTER UPDATE ON flow_control_slots FOR EACH ROW
BEGIN
  UPDATE flow_control_slots SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: flow_control_queue
CREATE TABLE IF NOT EXISTS flow_control_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id VARCHAR(255) NOT NULL,
  scope_key VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  priority INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(255) NOT NULL DEFAULT 'queued',
  enqueued_at INTEGER NOT NULL,
  dispatch_after INTEGER,
  expires_at INTEGER,
  payload TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_fc_queue_status ON flow_control_queue (status);
CREATE TRIGGER IF NOT EXISTS trg_flow_control_queue_update
AFTER UPDATE ON flow_control_queue FOR EACH ROW
BEGIN
  UPDATE flow_control_queue SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: cm_bench_domains
CREATE TABLE IF NOT EXISTS cm_bench_domains (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  created_by VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  owner_user_id VARCHAR(255) NOT NULL,
  status VARCHAR(255) NOT NULL DEFAULT 'active'
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_bench_domains_owner_domain ON cm_bench_domains (owner_user_id, domain_id);
CREATE TRIGGER IF NOT EXISTS trg_cm_bench_domains_update
AFTER UPDATE ON cm_bench_domains FOR EACH ROW
BEGIN
  UPDATE cm_bench_domains SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: cm_bench_templates
CREATE TABLE IF NOT EXISTS cm_bench_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id VARCHAR(255) NOT NULL,
  template_name VARCHAR(255) NOT NULL,
  display_name VARCHAR(255),
  description TEXT,
  category VARCHAR(255),
  target_type VARCHAR(255) NOT NULL DEFAULT 'agent_session',
  grading_type VARCHAR(255) NOT NULL DEFAULT 'automated',
  source VARCHAR(255) NOT NULL DEFAULT 'agentbench',
  source_path VARCHAR(255),
  source_hash VARCHAR(255),
  latest_version INTEGER NOT NULL DEFAULT 1,
  published_version INTEGER,
  status VARCHAR(255) NOT NULL DEFAULT 'draft',
  created_by VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  owner_user_id VARCHAR(255)
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_bench_templates_owner_domain_name ON cm_bench_templates (owner_user_id, domain_id, template_name);
CREATE INDEX IF NOT EXISTS idx_cm_bench_templates_domain_status ON cm_bench_templates (domain_id, status);
CREATE INDEX IF NOT EXISTS idx_cm_bench_templates_domain_name ON cm_bench_templates (domain_id, template_name);
CREATE TRIGGER IF NOT EXISTS trg_cm_bench_templates_update
AFTER UPDATE ON cm_bench_templates FOR EACH ROW
BEGIN
  UPDATE cm_bench_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: cm_bench_template_versions
CREATE TABLE IF NOT EXISTS cm_bench_template_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id VARCHAR(255) NOT NULL,
  template_name VARCHAR(255) NOT NULL,
  version INTEGER NOT NULL,
  display_name VARCHAR(255),
  description TEXT,
  content_md TEXT NOT NULL,
  parsed_meta_json TEXT,
  source_path VARCHAR(255),
  source_hash VARCHAR(255),
  status VARCHAR(255) NOT NULL DEFAULT 'draft',
  created_by VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  owner_user_id VARCHAR(255) NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_btv_owner_dom_name_ver ON cm_bench_template_versions (owner_user_id, domain_id, template_name, version);
CREATE INDEX IF NOT EXISTS idx_cm_bench_template_versions_domain_name_status ON cm_bench_template_versions (domain_id, template_name, status);
CREATE TRIGGER IF NOT EXISTS trg_cm_bench_template_versions_update
AFTER UPDATE ON cm_bench_template_versions FOR EACH ROW
BEGIN
  UPDATE cm_bench_template_versions SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: cm_bench_runs
CREATE TABLE IF NOT EXISTS cm_bench_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bench_run_id VARCHAR(255) NOT NULL,
  domain_id VARCHAR(255) NOT NULL,
  template_name VARCHAR(255) NOT NULL,
  template_version INTEGER NOT NULL,
  target_type VARCHAR(255) NOT NULL DEFAULT 'agent_session',
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  score INTEGER,
  max_score INTEGER,
  pass_rate INTEGER,
  model VARCHAR(255),
  suite VARCHAR(255),
  scene VARCHAR(255),
  triggered_by VARCHAR(255),
  clawmind_flow_id VARCHAR(255),
  session_id VARCHAR(255),
  session_key VARCHAR(255),
  run_config_json TEXT,
  summary_json TEXT,
  error_text TEXT,
  started_at INTEGER,
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  owner_user_id BLOB NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_bench_runs_run_id ON cm_bench_runs (bench_run_id);
CREATE INDEX IF NOT EXISTS idx_cm_bench_runs_domain_template ON cm_bench_runs (domain_id, template_name);
CREATE INDEX IF NOT EXISTS idx_cm_bench_runs_status ON cm_bench_runs (status);
CREATE INDEX IF NOT EXISTS idx_cm_bench_runs_clawmind_flow ON cm_bench_runs (clawmind_flow_id);
CREATE TRIGGER IF NOT EXISTS trg_cm_bench_runs_update
AFTER UPDATE ON cm_bench_runs FOR EACH ROW
BEGIN
  UPDATE cm_bench_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: cm_bench_task_results
CREATE TABLE IF NOT EXISTS cm_bench_task_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  result_id VARCHAR(255) NOT NULL,
  bench_run_id VARCHAR(255) NOT NULL,
  task_id VARCHAR(255) NOT NULL,
  task_name VARCHAR(255),
  status VARCHAR(255) NOT NULL,
  score INTEGER,
  max_score INTEGER,
  grading_type VARCHAR(255),
  execution_time_ms INTEGER,
  transcript_path VARCHAR(255),
  workspace_path VARCHAR(255),
  result_json TEXT,
  breakdown_json TEXT,
  notes TEXT,
  error_text TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_bench_task_results_result_id ON cm_bench_task_results (result_id);
CREATE INDEX IF NOT EXISTS idx_cm_bench_task_results_run ON cm_bench_task_results (bench_run_id);
CREATE INDEX IF NOT EXISTS idx_cm_bench_task_results_task ON cm_bench_task_results (task_id);
CREATE TRIGGER IF NOT EXISTS trg_cm_bench_task_results_update
AFTER UPDATE ON cm_bench_task_results FOR EACH ROW
BEGIN
  UPDATE cm_bench_task_results SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: cm_bench_artifacts
CREATE TABLE IF NOT EXISTS cm_bench_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id VARCHAR(255) NOT NULL,
  bench_run_id VARCHAR(255) NOT NULL,
  result_id VARCHAR(255),
  task_id VARCHAR(255),
  artifact_type VARCHAR(255) NOT NULL,
  filename VARCHAR(255),
  content_type VARCHAR(255),
  size_bytes INTEGER,
  storage_type VARCHAR(255) NOT NULL DEFAULT 'db',
  storage_path VARCHAR(255),
  content_text TEXT,
  content_json TEXT,
  summary_json TEXT,
  sha256 VARCHAR(255),
  created_by VARCHAR(255),
  owner_user_id VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_bench_artifacts_artifact_id ON cm_bench_artifacts (artifact_id);
CREATE INDEX IF NOT EXISTS idx_cm_bench_artifacts_run ON cm_bench_artifacts (bench_run_id);
CREATE INDEX IF NOT EXISTS idx_cm_bench_artifacts_run_type ON cm_bench_artifacts (bench_run_id, artifact_type);
CREATE INDEX IF NOT EXISTS idx_cm_bench_artifacts_task ON cm_bench_artifacts (bench_run_id, task_id);
CREATE INDEX IF NOT EXISTS idx_cm_bench_artifacts_owner_run ON cm_bench_artifacts (owner_user_id, bench_run_id);
CREATE TRIGGER IF NOT EXISTS trg_cm_bench_artifacts_update
AFTER UPDATE ON cm_bench_artifacts FOR EACH ROW
BEGIN
  UPDATE cm_bench_artifacts SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_workflow_templates
CREATE TABLE IF NOT EXISTS dev_workflow_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  phases_json TEXT NOT NULL,
  is_built_in INTEGER NOT NULL DEFAULT 0,
  created_by VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_wf_templates_template_id ON dev_workflow_templates (template_id);
CREATE INDEX IF NOT EXISTS idx_dev_wf_templates_built_in ON dev_workflow_templates (is_built_in);
CREATE TRIGGER IF NOT EXISTS trg_dev_workflow_templates_update
AFTER UPDATE ON dev_workflow_templates FOR EACH ROW
BEGIN
  UPDATE dev_workflow_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_workflows
CREATE TABLE IF NOT EXISTS dev_workflows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  title VARCHAR(255) NOT NULL,
  template_id VARCHAR(255) NOT NULL,
  dima_work_item_id VARCHAR(255) NOT NULL,
  dima_work_item_type VARCHAR(255) NOT NULL,
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  current_phase VARCHAR(255),
  enabled_phases_json TEXT NOT NULL,
  config_json TEXT,
  git_repo_url VARCHAR(255),
  git_branch VARCHAR(255),
  pr_url VARCHAR(255),
  pr_id VARCHAR(255),
  timeout_hours INTEGER NOT NULL DEFAULT 72,
  owner_user_id VARCHAR(255) NOT NULL,
  started_at INTEGER,
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_workflows_workflow_id ON dev_workflows (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_workflows_status ON dev_workflows (status);
CREATE INDEX IF NOT EXISTS idx_dev_workflows_dima_id ON dev_workflows (dima_work_item_id);
CREATE INDEX IF NOT EXISTS idx_dev_workflows_template ON dev_workflows (template_id);
CREATE INDEX IF NOT EXISTS idx_dev_workflows_owner ON dev_workflows (owner_user_id);
CREATE TRIGGER IF NOT EXISTS trg_dev_workflows_update
AFTER UPDATE ON dev_workflows FOR EACH ROW
BEGIN
  UPDATE dev_workflows SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_workflow_phases
CREATE TABLE IF NOT EXISTS dev_workflow_phases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(255) NOT NULL,
  phase_name VARCHAR(255) NOT NULL,
  phase_order INTEGER NOT NULL,
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  enabled INTEGER NOT NULL DEFAULT 1,
  required INTEGER NOT NULL DEFAULT 1,
  has_human_gate INTEGER NOT NULL DEFAULT 0,
  has_bot_execution INTEGER NOT NULL DEFAULT 1,
  bot_role VARCHAR(255),
  default_timeout_minutes INTEGER NOT NULL DEFAULT 10,
  document_url VARCHAR(255),
  document_title VARCHAR(255),
  result_summary TEXT,
  baas_run_id VARCHAR(255),
  error_message TEXT,
  confirmed_by VARCHAR(255),
  confirm_comment TEXT,
  rejected_by VARCHAR(255),
  reject_reason TEXT,
  reject_to_phase_id VARCHAR(255),
  version INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER,
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_wf_phases_wf_phase ON dev_workflow_phases (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_wf_phases_workflow ON dev_workflow_phases (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_wf_phases_status ON dev_workflow_phases (workflow_id, status);
CREATE TRIGGER IF NOT EXISTS trg_dev_workflow_phases_update
AFTER UPDATE ON dev_workflow_phases FOR EACH ROW
BEGIN
  UPDATE dev_workflow_phases SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_phase_conversations
CREATE TABLE IF NOT EXISTS dev_phase_conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(255) NOT NULL,
  baas_message_id VARCHAR(255),
  role VARCHAR(255) NOT NULL,
  sender_id VARCHAR(255) NOT NULL,
  sender_name VARCHAR(255),
  content TEXT NOT NULL,
  session_id VARCHAR(255),
  bot_id VARCHAR(255),
  metadata_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_dev_phase_conv_workflow ON dev_phase_conversations (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_phase_conv_wf_phase ON dev_phase_conversations (workflow_id, phase_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_phase_conv_baas_msg ON dev_phase_conversations (baas_message_id);
CREATE INDEX IF NOT EXISTS idx_dev_phase_conv_session ON dev_phase_conversations (session_id);
CREATE TRIGGER IF NOT EXISTS trg_dev_phase_conversations_update
AFTER UPDATE ON dev_phase_conversations FOR EACH ROW
BEGIN
  UPDATE dev_phase_conversations SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_approvals
CREATE TABLE IF NOT EXISTS dev_approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(255) NOT NULL,
  target_type VARCHAR(255) NOT NULL,
  target_id VARCHAR(255) NOT NULL,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  block_phase INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0,
  created_by VARCHAR(255) NOT NULL,
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_approvals_approval_id ON dev_approvals (approval_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_workflow ON dev_approvals (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_wf_phase ON dev_approvals (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_target ON dev_approvals (target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_status ON dev_approvals (status);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_created_by ON dev_approvals (created_by);
CREATE TRIGGER IF NOT EXISTS trg_dev_approvals_update
AFTER UPDATE ON dev_approvals FOR EACH ROW
BEGIN
  UPDATE dev_approvals SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_approval_reviewers
CREATE TABLE IF NOT EXISTS dev_approval_reviewers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id VARCHAR(255) NOT NULL,
  reviewer_id VARCHAR(255) NOT NULL,
  reviewer_name VARCHAR(255),
  notified INTEGER NOT NULL DEFAULT 0,
  decision VARCHAR(255),
  comment TEXT,
  decided_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_approval_rev_approval_user ON dev_approval_reviewers (approval_id, reviewer_id);
CREATE INDEX IF NOT EXISTS idx_dev_approval_rev_reviewer ON dev_approval_reviewers (reviewer_id);
CREATE TRIGGER IF NOT EXISTS trg_dev_approval_reviewers_update
AFTER UPDATE ON dev_approval_reviewers FOR EACH ROW
BEGIN
  UPDATE dev_approval_reviewers SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_discussions
CREATE TABLE IF NOT EXISTS dev_discussions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  discussion_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(255) NOT NULL,
  topic VARCHAR(255) NOT NULL,
  description TEXT,
  context_type VARCHAR(255),
  context_id VARCHAR(255),
  status VARCHAR(255) NOT NULL DEFAULT 'open',
  conclusion TEXT,
  created_by VARCHAR(255) NOT NULL,
  closed_by VARCHAR(255),
  closed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_discussions_disc_id ON dev_discussions (discussion_id);
CREATE INDEX IF NOT EXISTS idx_dev_discussions_workflow ON dev_discussions (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_discussions_wf_phase ON dev_discussions (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_discussions_status ON dev_discussions (status);
CREATE TRIGGER IF NOT EXISTS trg_dev_discussions_update
AFTER UPDATE ON dev_discussions FOR EACH ROW
BEGIN
  UPDATE dev_discussions SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_discussion_replies
CREATE TABLE IF NOT EXISTS dev_discussion_replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  discussion_id VARCHAR(255) NOT NULL,
  author_id VARCHAR(255) NOT NULL,
  author_name VARCHAR(255),
  content TEXT NOT NULL,
  parent_reply_id INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_dev_disc_rep_disc ON dev_discussion_replies (discussion_id);
CREATE INDEX IF NOT EXISTS idx_dev_disc_rep_author ON dev_discussion_replies (author_id);
CREATE INDEX IF NOT EXISTS idx_dev_disc_rep_parent ON dev_discussion_replies (parent_reply_id);
CREATE TRIGGER IF NOT EXISTS trg_dev_discussion_replies_update
AFTER UPDATE ON dev_discussion_replies FOR EACH ROW
BEGIN
  UPDATE dev_discussion_replies SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_git_ops
CREATE TABLE IF NOT EXISTS dev_git_ops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(255) NOT NULL,
  operation VARCHAR(255) NOT NULL,
  repo_url VARCHAR(255) NOT NULL,
  branch VARCHAR(255) NOT NULL,
  commit_sha VARCHAR(255),
  commit_message VARCHAR(255),
  remote_branch VARCHAR(255),
  summary TEXT,
  result VARCHAR(255) NOT NULL DEFAULT 'success',
  error_message TEXT,
  executed_by VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_dev_git_ops_workflow ON dev_git_ops (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_git_ops_wf_phase ON dev_git_ops (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_git_ops_commit ON dev_git_ops (commit_sha);
CREATE TRIGGER IF NOT EXISTS trg_dev_git_ops_update
AFTER UPDATE ON dev_git_ops FOR EACH ROW
BEGIN
  UPDATE dev_git_ops SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_artifacts
CREATE TABLE IF NOT EXISTS dev_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(255) NOT NULL,
  artifact_type VARCHAR(255) NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  title VARCHAR(255) NOT NULL,
  content TEXT,
  content_url VARCHAR(255),
  format VARCHAR(255) NOT NULL DEFAULT 'markdown',
  status VARCHAR(255) NOT NULL DEFAULT 'current',
  source VARCHAR(255) NOT NULL DEFAULT 'bot',
  authored_by VARCHAR(255) NOT NULL,
  archived_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_dev_artifacts_workflow ON dev_artifacts (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_artifacts_wf_phase ON dev_artifacts (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_artifacts_type_status ON dev_artifacts (workflow_id, artifact_type, status);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_artifacts_wf_phase_type_ver ON dev_artifacts (workflow_id, phase_id, artifact_type, version);
CREATE TRIGGER IF NOT EXISTS trg_dev_artifacts_update
AFTER UPDATE ON dev_artifacts FOR EACH ROW
BEGIN
  UPDATE dev_artifacts SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: dev_project_constraints
CREATE TABLE IF NOT EXISTS dev_project_constraints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  constraints_json TEXT NOT NULL,
  change_summary VARCHAR(255),
  changed_by VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_dev_proj_constraints_workflow ON dev_project_constraints (workflow_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_proj_constraints_wf_ver ON dev_project_constraints (workflow_id, version);
CREATE TRIGGER IF NOT EXISTS trg_dev_project_constraints_update
AFTER UPDATE ON dev_project_constraints FOR EACH ROW
BEGIN
  UPDATE dev_project_constraints SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: insight_failure_task
CREATE TABLE IF NOT EXISTS insight_failure_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_dt CHAR NOT NULL,
  owner_user_id VARCHAR(255) NOT NULL,
  bot_id VARCHAR(255) NOT NULL,
  bot_name VARCHAR(255) NOT NULL,
  session_id VARCHAR(255) NOT NULL,
  task_index INTEGER NOT NULL DEFAULT 0,
  task_description VARCHAR(255) NOT NULL,
  is_complete TINYINT NOT NULL DEFAULT 2,
  failure_class VARCHAR(255) NOT NULL DEFAULT 'UNKNOWN',
  judge_reason_summary VARCHAR(255),
  session_start_time VARCHAR(255),
  session_end_time VARCHAR(255),
  session_duration_seconds INTEGER,
  is_cron TINYINT NOT NULL DEFAULT 0,
  payload_ref VARCHAR(255) NOT NULL,
  payload_etag VARCHAR(255) NOT NULL,
  payload_version_id VARCHAR(255),
  batch_id VARCHAR(255) NOT NULL,
  data_as_of VARCHAR(255) NOT NULL,
  judged_at VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_insight_failure_owner_dt ON insight_failure_task (owner_user_id, source_dt, is_complete);
CREATE INDEX IF NOT EXISTS idx_insight_failure_owner_bot_dt ON insight_failure_task (owner_user_id, bot_id, source_dt, is_complete);
CREATE INDEX IF NOT EXISTS idx_insight_failure_session_task ON insight_failure_task (owner_user_id, session_id, task_index);
CREATE TRIGGER IF NOT EXISTS trg_insight_failure_task_update
AFTER UPDATE ON insight_failure_task FOR EACH ROW
BEGIN
  UPDATE insight_failure_task SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: insight_improvement_item
CREATE TABLE IF NOT EXISTS insight_improvement_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id VARCHAR(255) NOT NULL,
  bot_owner_user_id VARCHAR(255) NOT NULL,
  bot_id VARCHAR(255) NOT NULL,
  title VARCHAR(255) NOT NULL,
  user_guidance TEXT,
  source_type VARCHAR(255) NOT NULL DEFAULT 'USER_SELECTED',
  source_rule_id VARCHAR(255),
  evidence_count INTEGER NOT NULL DEFAULT 0,
  session_count INTEGER NOT NULL DEFAULT 0,
  data_start_time VARCHAR(255),
  data_end_time VARCHAR(255),
  data_as_of VARCHAR(255) NOT NULL,
  batch_id VARCHAR(255) NOT NULL,
  content_fingerprint CHAR NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL,
  status VARCHAR(255) NOT NULL DEFAULT 'ACTIVE',
  applied_evolve_task_id VARCHAR(255),
  apply_request_id VARCHAR(255),
  applied_by VARCHAR(255),
  applied_at INTEGER,
  version INTEGER NOT NULL DEFAULT 1,
  created_by VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_insight_improvement_owner_bot ON insight_improvement_item (owner_user_id, bot_id, status, gmt_create);
CREATE TRIGGER IF NOT EXISTS trg_insight_improvement_item_update
AFTER UPDATE ON insight_improvement_item FOR EACH ROW
BEGIN
  UPDATE insight_improvement_item SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: insight_improvement_evidence
CREATE TABLE IF NOT EXISTS insight_improvement_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  improvement_id INTEGER NOT NULL,
  session_id VARCHAR(255) NOT NULL,
  task_index INTEGER NOT NULL DEFAULT 0,
  ordinal INTEGER NOT NULL DEFAULT 0,
  task_description_snapshot VARCHAR(255) NOT NULL,
  failure_class_snapshot VARCHAR(255) NOT NULL,
  reasoning_summary VARCHAR(255),
  payload_ref VARCHAR(255) NOT NULL,
  payload_etag VARCHAR(255) NOT NULL,
  payload_version_id VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_insight_evidence_session_task ON insight_improvement_evidence (session_id, task_index);
CREATE INDEX IF NOT EXISTS idx_insight_evidence_improvement_order ON insight_improvement_evidence (improvement_id, ordinal);
CREATE TRIGGER IF NOT EXISTS trg_insight_improvement_evidence_update
AFTER UPDATE ON insight_improvement_evidence FOR EACH ROW
BEGIN
  UPDATE insight_improvement_evidence SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: insight_improvement_evolve_link
CREATE TABLE IF NOT EXISTS insight_improvement_evolve_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  improvement_id INTEGER NOT NULL,
  evolve_task_id VARCHAR(255) NOT NULL,
  request_id VARCHAR(255) NOT NULL,
  created_by VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_insight_evolve_task_id ON insight_improvement_evolve_link (evolve_task_id);
CREATE TRIGGER IF NOT EXISTS trg_insight_improvement_evolve_link_update
AFTER UPDATE ON insight_improvement_evolve_link FOR EACH ROW
BEGIN
  UPDATE insight_improvement_evolve_link SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: insight_metric_daily
CREATE TABLE IF NOT EXISTS insight_metric_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_dt CHAR NOT NULL,
  owner_user_id VARCHAR(255) NOT NULL,
  bot_id VARCHAR(255) NOT NULL,
  bot_name VARCHAR(255) NOT NULL,
  is_cron TINYINT NOT NULL DEFAULT 0,
  total_task_count INTEGER NOT NULL DEFAULT 0,
  valid_task_count INTEGER NOT NULL DEFAULT 0,
  complete_task_count INTEGER NOT NULL DEFAULT 0,
  capability_task_count INTEGER NOT NULL DEFAULT 0,
  capability_complete_task_count INTEGER NOT NULL DEFAULT 0,
  auto_complete_task_count INTEGER NOT NULL DEFAULT 0,
  failure_distribution_json TEXT,
  batch_id VARCHAR(255) NOT NULL,
  data_as_of VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_insight_metric_owner_dt ON insight_metric_daily (owner_user_id, source_dt, is_cron);
CREATE INDEX IF NOT EXISTS idx_insight_metric_owner_bot_dt ON insight_metric_daily (owner_user_id, bot_id, source_dt, is_cron);
CREATE TRIGGER IF NOT EXISTS trg_insight_metric_daily_update
AFTER UPDATE ON insight_metric_daily FOR EACH ROW
BEGIN
  UPDATE insight_metric_daily SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: ce_task_sources
CREATE TABLE IF NOT EXISTS ce_task_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id VARCHAR(255) NOT NULL,
  source_type VARCHAR(255) NOT NULL,
  source_id VARCHAR(255) NOT NULL,
  source_schema_version VARCHAR(255) NOT NULL,
  adapter_version VARCHAR(255),
  source_ref_json TEXT NOT NULL,
  source_digest VARCHAR(255),
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  error_code VARCHAR(255),
  error_message TEXT,
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_ce_task_sources_origin ON ce_task_sources (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_ce_task_sources_status ON ce_task_sources (status, gmt_create);
CREATE TRIGGER IF NOT EXISTS trg_ce_task_sources_update
AFTER UPDATE ON ce_task_sources FOR EACH ROW
BEGIN
  UPDATE ce_task_sources SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- Table: ce_repair_tool_calls
CREATE TABLE IF NOT EXISTS ce_repair_tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id VARCHAR(255) NOT NULL,
  task_id VARCHAR(255) NOT NULL,
  step_id VARCHAR(255) NOT NULL,
  execution_id VARCHAR(255) NOT NULL,
  authorization_scope_digest VARCHAR(255) NOT NULL,
  client_request_id VARCHAR(255) NOT NULL,
  tool_name VARCHAR(255) NOT NULL,
  operation VARCHAR(255) NOT NULL,
  action_id VARCHAR(255),
  deadline_at INTEGER,
  request_json TEXT NOT NULL,
  is_write INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(255) NOT NULL DEFAULT 'pending',
  lease_owner VARCHAR(255),
  lease_expires_at INTEGER,
  result_json TEXT,
  result_digest VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ce_repair_tool_calls_call_id ON ce_repair_tool_calls (call_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ce_repair_tool_calls_step_client ON ce_repair_tool_calls (step_id, client_request_id);
CREATE TRIGGER IF NOT EXISTS trg_ce_repair_tool_calls_update
AFTER UPDATE ON ce_repair_tool_calls FOR EACH ROW
BEGIN
  UPDATE ce_repair_tool_calls SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── TcLog tables (traces, observations, biz_refs) ──────────────────

-- Table: ac_otel_log_trace — OCB OpenTelemetry traces
CREATE TABLE IF NOT EXISTS ac_otel_log_trace (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id VARCHAR(255) NOT NULL,
  session_id VARCHAR(255),
  session_key VARCHAR(255),
  bot_id VARCHAR(255),
  user_id VARCHAR(255),
  engine VARCHAR(255),
  status VARCHAR(255),
  level VARCHAR(255),
  name VARCHAR(255),
  input TEXT,
  output TEXT,
  start_time_ms INTEGER,
  end_time_ms INTEGER,
  latency_ms INTEGER,
  usage_input_tokens INTEGER,
  usage_output_tokens INTEGER,
  usage_cache_read_tokens INTEGER,
  usage_cache_write_tokens INTEGER,
  usage_total_tokens INTEGER,
  total_cost REAL,
  biz_scene VARCHAR(255),
  biz_task_id VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_otel_trace_trace_id ON ac_otel_log_trace (trace_id);
CREATE INDEX IF NOT EXISTS idx_otel_trace_session_id ON ac_otel_log_trace (session_id);
CREATE INDEX IF NOT EXISTS idx_otel_trace_session_key ON ac_otel_log_trace (session_key);
CREATE INDEX IF NOT EXISTS idx_otel_trace_user_id ON ac_otel_log_trace (user_id);
CREATE INDEX IF NOT EXISTS idx_otel_trace_bot_id ON ac_otel_log_trace (bot_id);
CREATE INDEX IF NOT EXISTS idx_otel_trace_start_time ON ac_otel_log_trace (start_time_ms);
CREATE INDEX IF NOT EXISTS idx_otel_trace_biz_task ON ac_otel_log_trace (biz_scene, biz_task_id);

-- Table: ac_otel_log_observation — OCB OpenTelemetry observations
CREATE TABLE IF NOT EXISTS ac_otel_log_observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id VARCHAR(255) NOT NULL,
  trace_id VARCHAR(255) NOT NULL,
  parent_observation_id VARCHAR(255),
  type VARCHAR(255),
  name VARCHAR(255),
  model VARCHAR(255),
  status VARCHAR(255),
  status_message VARCHAR(255),
  start_time_ms INTEGER,
  end_time_ms INTEGER,
  latency_ms INTEGER,
  start_time INTEGER,
  end_time INTEGER,
  input TEXT,
  output TEXT,
  metadata TEXT,
  usage_input_tokens INTEGER,
  usage_output_tokens INTEGER,
  usage_total_tokens INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_otel_obs_trace_id ON ac_otel_log_observation (trace_id);
CREATE INDEX IF NOT EXISTS idx_otel_obs_observation_id ON ac_otel_log_observation (observation_id);

-- Table: ac_otel_log_biz_ref — business task to trace/session references
CREATE TABLE IF NOT EXISTS ac_otel_log_biz_ref (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  biz_scene VARCHAR(255) NOT NULL,
  biz_task_id VARCHAR(255) NOT NULL,
  ref_type VARCHAR(255) NOT NULL,
  ref_value VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_otel_biz_ref_scene_task ON ac_otel_log_biz_ref (biz_scene, biz_task_id);

-- Table: aw_langfuse_traces — legacy Langfuse traces
CREATE TABLE IF NOT EXISTS aw_langfuse_traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id VARCHAR(255) NOT NULL,
  session_id VARCHAR(255),
  real_session_id VARCHAR(255),
  bot_id VARCHAR(255),
  user_id VARCHAR(255),
  status VARCHAR(255),
  name VARCHAR(255),
  input TEXT,
  output TEXT,
  gmt_trace INTEGER,
  latency REAL,
  usage_input_tokens INTEGER,
  usage_output_tokens INTEGER,
  usage_cache_read_tokens INTEGER,
  usage_cache_write_tokens INTEGER,
  usage_total_tokens INTEGER,
  total_cost REAL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_langfuse_trace_trace_id ON aw_langfuse_traces (trace_id);
CREATE INDEX IF NOT EXISTS idx_langfuse_trace_session_id ON aw_langfuse_traces (session_id);
CREATE INDEX IF NOT EXISTS idx_langfuse_trace_real_session_id ON aw_langfuse_traces (real_session_id);
CREATE INDEX IF NOT EXISTS idx_langfuse_trace_user_id ON aw_langfuse_traces (user_id);
CREATE INDEX IF NOT EXISTS idx_langfuse_trace_bot_id ON aw_langfuse_traces (bot_id);
CREATE INDEX IF NOT EXISTS idx_langfuse_trace_gmt_trace ON aw_langfuse_traces (gmt_trace);

-- Table: aw_langfuse_observation — legacy Langfuse observations
CREATE TABLE IF NOT EXISTS aw_langfuse_observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id VARCHAR(255) NOT NULL,
  trace_id VARCHAR(255) NOT NULL,
  parent_observation_id VARCHAR(255),
  type VARCHAR(255),
  name VARCHAR(255),
  model VARCHAR(255),
  status VARCHAR(255),
  status_message VARCHAR(255),
  start_time_ms INTEGER,
  end_time_ms INTEGER,
  latency_ms INTEGER,
  start_time INTEGER,
  end_time INTEGER,
  input TEXT,
  output TEXT,
  metadata TEXT,
  usage_input_tokens INTEGER,
  usage_output_tokens INTEGER,
  usage_total_tokens INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_langfuse_obs_trace_id ON aw_langfuse_observation (trace_id);
CREATE INDEX IF NOT EXISTS idx_langfuse_obs_observation_id ON aw_langfuse_observation (observation_id);


-- Table: bot_workflow_permissions
-- Required by BotWorkflowPermissionRepository.
CREATE TABLE IF NOT EXISTS bot_workflow_permissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT,
  bot_owner_id TEXT NOT NULL,
  workflow_id TEXT NOT NULL,
  env TEXT NOT NULL,
  can_view INTEGER NOT NULL DEFAULT 0,
  can_execute INTEGER NOT NULL DEFAULT 0,
  can_edit INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_bot_wf_perm_workflow ON bot_workflow_permissions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_bot_wf_perm_owner ON bot_workflow_permissions(bot_owner_id, workflow_id);
CREATE INDEX IF NOT EXISTS idx_bot_wf_perm_bot ON bot_workflow_permissions(bot_id, bot_owner_id, workflow_id);
