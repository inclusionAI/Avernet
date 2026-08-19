-- ClawFlow Schema DDL — MySQL / ZDAS
-- Aligned with src/db/schema.ts migrations v1–v4 and ClawWeb server/schema.ts migrations v5–v7
-- Compliance: id BIGINT, gmt_create/gmt_modified TIMESTAMP, COMMENT on all columns,
--             gmt_modified ON UPDATE CURRENT_TIMESTAMP, no FLOAT/DOUBLE
--             Indexed columns use VARCHAR(255) not TEXT (OceanBase can't index TEXT)
-- Note: Indexes defined inline within CREATE TABLE to avoid ZDAS parser issues
--       with separate CREATE INDEX statements.
-- Usage: mysql -u <user> -p <database> < init_mysql.sql

-- ── Migration v1: flow_events, flow_metrics, triggered_alerts ──

CREATE TABLE IF NOT EXISTS flow_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(255) NOT NULL COMMENT '事件唯一标识',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) COMMENT '节点ID',
  event_type VARCHAR(255) NOT NULL COMMENT '事件类型',
  attempt INTEGER COMMENT '重试次数',
  time INTEGER NOT NULL COMMENT '事件发生时间(unix秒)',
  data_json TEXT COMMENT '事件数据JSON',
  error_text TEXT COMMENT '错误信息',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_flow_events_flow_id (flow_id),
  INDEX idx_flow_events_workflow_id (workflow_id),
  INDEX idx_flow_events_time (time)
);

CREATE TABLE IF NOT EXISTS flow_metrics (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  metric_name VARCHAR(255) NOT NULL COMMENT '指标名称',
  metric_value DECIMAL(20,6) NOT NULL COMMENT '指标值',
  time INTEGER NOT NULL COMMENT '指标采集时间(unix秒)',
  labels_json TEXT COMMENT '标签JSON',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_flow_metrics_workflow (workflow_id),
  INDEX idx_flow_metrics_name (metric_name),
  INDEX idx_flow_metrics_time (time)
);

CREATE TABLE IF NOT EXISTS triggered_alerts (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) COMMENT '节点ID',
  alert_rule VARCHAR(255) NOT NULL COMMENT '告警规则名称',
  severity VARCHAR(255) NOT NULL DEFAULT 'warning' COMMENT '严重程度',
  message TEXT NOT NULL COMMENT '告警消息',
  time INTEGER NOT NULL COMMENT '告警触发时间(unix秒)',
  acknowledged INTEGER NOT NULL DEFAULT 0 COMMENT '是否已确认(0未确认1已确认)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_triggered_alerts_workflow (workflow_id),
  INDEX idx_triggered_alerts_ack (acknowledged),
  INDEX idx_triggered_alerts_time (time)
);

-- ── Migration v2: node_executions, flow_runs ──

CREATE TABLE IF NOT EXISTS node_executions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  executor_type VARCHAR(255) COMMENT '执行器类型',
  status VARCHAR(255) NOT NULL COMMENT '执行状态',
  attempt INTEGER NOT NULL DEFAULT 1 COMMENT '尝试次数',
  input_json TEXT COMMENT '输入数据JSON',
  output_json TEXT COMMENT '输出数据JSON',
  error_text TEXT COMMENT '错误信息',
  duration_ms BIGINT COMMENT '执行耗时(毫秒)',
  token_usage_json TEXT COMMENT 'Token用量JSON',
  started_at BIGINT NOT NULL COMMENT '开始时间(unix秒)',
  completed_at BIGINT COMMENT '完成时间(unix秒)',
  triggered_by VARCHAR(255) COMMENT '触发来源节点ID',
  node_title VARCHAR(255) COMMENT '节点标题',
  progress_message TEXT COMMENT '进度消息',
  resolved_prompt TEXT COMMENT '模板解析后的实际Prompt文本',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_node_exec_flow_id (flow_id),
  INDEX idx_node_exec_workflow_id (workflow_id),
  INDEX idx_node_exec_node_status (flow_id, node_id, status),
  INDEX idx_node_exec_created (gmt_create)
);

CREATE TABLE IF NOT EXISTS flow_runs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  workflow_title VARCHAR(255) COMMENT '工作流标题',
  status VARCHAR(255) NOT NULL COMMENT '运行状态',
  params_json TEXT COMMENT '参数JSON',
  input_json TEXT COMMENT '输入数据JSON',
  result_json TEXT COMMENT '结果JSON',
  node_count INTEGER NOT NULL DEFAULT 0 COMMENT '节点总数',
  succeeded_count INTEGER NOT NULL DEFAULT 0 COMMENT '成功节点数',
  failed_count INTEGER NOT NULL DEFAULT 0 COMMENT '失败节点数',
  total_duration_ms BIGINT COMMENT '总耗时(毫秒)',
  total_token_usage BIGINT COMMENT '总Token用量',
  triggered_by VARCHAR(255) COMMENT '触发来源',
  identity_key TEXT COMMENT '工作流身份键(用于分组)',
  current_phase VARCHAR(255) COMMENT '当前执行阶段',
  started_at BIGINT NOT NULL COMMENT '开始时间(unix秒)',
  completed_at BIGINT COMMENT '完成时间(unix秒)',
  credentials_json TEXT COMMENT '凭证JSON',
  origin_session_key VARCHAR(255) COMMENT '来源会话键',
  origin_session_id VARCHAR(255) COMMENT '来源会话ID',
  origin_bot_id VARCHAR(255) COMMENT '来源Bot ID',
  user_id VARCHAR(255) COMMENT '用户ID',
  plugin_version VARCHAR(255) DEFAULT NULL COMMENT '插件版本号',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_flow_runs_flow_id (flow_id),
  INDEX idx_flow_runs_workflow_id (workflow_id),
  INDEX idx_flow_runs_status (status),
  INDEX idx_flow_runs_started (started_at)
);

-- ── Migration v3: scheduled_triggers ──

CREATE TABLE IF NOT EXISTS scheduled_triggers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  trigger_id VARCHAR(255) NOT NULL COMMENT '触发器ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  pack_id VARCHAR(255) NOT NULL COMMENT '包ID',
  cron_expression VARCHAR(255) NOT NULL COMMENT 'Cron表达式',
  timezone VARCHAR(255) NOT NULL DEFAULT 'UTC' COMMENT '时区',
  params_json TEXT COMMENT '参数JSON',
  max_concurrent INTEGER NOT NULL DEFAULT 1 COMMENT '最大并发数',
  enabled INTEGER NOT NULL DEFAULT 1 COMMENT '是否启用(0禁用1启用)',
  last_fire_time INTEGER COMMENT '上次触发时间(unix秒)',
  next_fire_time INTEGER COMMENT '下次触发时间(unix秒)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_sched_triggers_workflow (workflow_id),
  INDEX idx_sched_triggers_enabled_next (enabled, next_fire_time),
  UNIQUE INDEX uk_sched_triggers_trigger_id (trigger_id)
);

-- ── Migration v4: webhook_triggers, webhook_events ──

CREATE TABLE IF NOT EXISTS webhook_triggers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  trigger_id VARCHAR(255) NOT NULL COMMENT '触发器ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  pack_id VARCHAR(255) COMMENT '包ID',
  secret VARCHAR(255) COMMENT 'Webhook签名密钥',
  payload_mapping TEXT COMMENT '请求体映射JSON',
  allowed_ips TEXT COMMENT '允许的IP列表JSON',
  enabled INTEGER NOT NULL DEFAULT 1 COMMENT '是否启用(0禁用1启用)',
  description TEXT COMMENT '描述信息',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_webhook_triggers_workflow (workflow_id),
  UNIQUE INDEX uk_webhook_triggers_trigger_id (trigger_id)
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(255) NOT NULL COMMENT '事件ID',
  trigger_id VARCHAR(255) NOT NULL COMMENT '触发器ID',
  flow_id VARCHAR(255) COMMENT '流程实例ID',
  status VARCHAR(255) NOT NULL COMMENT '处理状态',
  request_method VARCHAR(255) NOT NULL COMMENT '请求方法',
  request_headers TEXT COMMENT '请求头JSON',
  request_body_hash VARCHAR(255) COMMENT '请求体SHA256哈希',
  response_code INTEGER COMMENT '响应状态码',
  error_message TEXT COMMENT '错误信息',
  ip_address VARCHAR(255) COMMENT '来源IP地址',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_webhook_events_trigger (trigger_id),
  INDEX idx_webhook_events_event_id (event_id),
  INDEX idx_webhook_events_created (gmt_create),
  INDEX idx_webhook_events_dedup (event_id, gmt_create)
);

-- ── Migration v5: workflow_specs (ClawWeb) ──

CREATE TABLE IF NOT EXISTS workflow_specs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  pack_id VARCHAR(255) COMMENT '包ID',
  spec_json MEDIUMTEXT NOT NULL COMMENT '工作流规格JSON',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_workflow_specs_workflow_id (workflow_id)
);

-- ── Migration v8: knowledge_bases (ClawWeb — GRT knowledge base configuration) ──

CREATE TABLE IF NOT EXISTS knowledge_bases (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  kb_id VARCHAR(255) NOT NULL COMMENT '知识库唯一标识(业务ID,用于YAML引用)',
  name VARCHAR(255) NOT NULL COMMENT '知识库名称(显示用)',
  description TEXT COMMENT '知识库描述',
  instance_name VARCHAR(255) NOT NULL COMMENT 'GRT实例名称',
  interface_name VARCHAR(255) NOT NULL COMMENT 'GRT接口名称',
  token VARCHAR(255) NOT NULL COMMENT 'GRT API Token',
  user_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '调用者花名',
  user_id VARCHAR(255) NOT NULL DEFAULT '' COMMENT '调用者工号',
  top_k INTEGER NOT NULL DEFAULT 3 COMMENT '返回结果数量',
  ranking_threshold DECIMAL(10,6) NOT NULL DEFAULT 0.01 COMMENT '精排置信度阈值',
  vector_threshold DECIMAL(10,6) NOT NULL DEFAULT 0.6 COMMENT '向量相似度阈值',
  ranking_model VARCHAR(255) NOT NULL DEFAULT 'bge-reranker-base' COMMENT '精排模型名称',
  env VARCHAR(255) NOT NULL DEFAULT 'prod' COMMENT '环境(prod/pre)',
  enabled INTEGER NOT NULL DEFAULT 1 COMMENT '是否启用(0禁用1启用)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_knowledge_bases_kb_id (kb_id),
  INDEX idx_knowledge_bases_enabled (enabled),
  INDEX idx_knowledge_bases_created (gmt_create)
);

-- Seed: default GRT knowledge base entry (disabled by default, enable after configuring token)
INSERT IGNORE INTO knowledge_bases (kb_id, name, description, instance_name, interface_name, token, user_name, user_id, top_k, ranking_threshold, vector_threshold, ranking_model, env, enabled)
VALUES ('my-grt-kb', 'Default GRT Knowledge Base', 'Default GRT knowledge base for ClawFlow workflows', 'TeamClawBot', 'ALL_TeamClawBot', '', '', '', 3, 0.01, 0.6, 'bge-reranker-base', 'prod', 0);

-- ── schema_version (migration tracker) ──

CREATE TABLE IF NOT EXISTS schema_version (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  version INTEGER NOT NULL COMMENT '版本号',
  description TEXT COMMENT '版本描述',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间'
);

-- ── ClawMind migration v11: node_step_traces

CREATE TABLE IF NOT EXISTS node_step_traces (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  attempt INTEGER NOT NULL DEFAULT 1 COMMENT '尝试次数',
  step_seq INTEGER NOT NULL COMMENT '步骤序号',
  step_type VARCHAR(32) NOT NULL COMMENT '步骤类型',
  skill_name VARCHAR(255) COMMENT '技能名称',
  tool_name VARCHAR(255) COMMENT '工具名称',
  tool_use_id VARCHAR(255) COMMENT '工具调用ID',
  tool_input_json TEXT COMMENT '工具输入JSON',
  tool_output_text TEXT COMMENT '工具输出文本',
  is_error INTEGER NOT NULL DEFAULT 0 COMMENT '是否错误(0否1是)',
  text_content TEXT COMMENT '文本内容',
  latency_ms BIGINT COMMENT '延迟(毫秒)',
  prompt_tokens BIGINT COMMENT '提示Token数',
  completion_tokens BIGINT COMMENT '补全Token数',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_nst_flow_node (flow_id, node_id, attempt),
  INDEX idx_nst_flow_id (flow_id),
  INDEX idx_nst_skill_name (skill_name),
  INDEX idx_nst_created (gmt_create)
);

-- ── ClawMind migration v12: facade_bindings

CREATE TABLE IF NOT EXISTS facade_bindings (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  command VARCHAR(255) NOT NULL COMMENT 'Slash命令(如/marketing-dispatch)',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  pack_id VARCHAR(255) COMMENT '包ID',
  remark TEXT COMMENT '备注',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_facade_bindings_command (command),
  INDEX idx_facade_bindings_workflow (workflow_id)
);

-- ── ClawMind migration v15: flow_control_slots, flow_control_queue

CREATE TABLE IF NOT EXISTS flow_control_slots (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  instance_id VARCHAR(255) NOT NULL COMMENT '实例ID',
  scope_key VARCHAR(255) NOT NULL COMMENT '范围键',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) COMMENT '节点ID',
  acquired_at BIGINT NOT NULL COMMENT '获取时间(unix秒)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_fc_slots_instance_scope_flow_node (instance_id, scope_key, flow_id, node_id),
  INDEX idx_fc_slots_instance_scope (instance_id, scope_key)
);

CREATE TABLE IF NOT EXISTS flow_control_queue (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  instance_id VARCHAR(255) NOT NULL COMMENT '实例ID',
  scope_key VARCHAR(255) NOT NULL COMMENT '范围键',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) COMMENT '节点ID',
  priority INTEGER NOT NULL DEFAULT 0 COMMENT '优先级',
  status VARCHAR(32) NOT NULL DEFAULT 'queued' COMMENT '状态(queued/dispatched/cancelled)',
  enqueued_at BIGINT NOT NULL COMMENT '入队时间(unix秒)',
  dispatch_after BIGINT COMMENT '调度延迟时间(unix秒)',
  expires_at BIGINT COMMENT '过期时间(unix秒)',
  payload TEXT COMMENT '负载数据',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_fc_queue_instance_scope_status (instance_id, scope_key, status, priority, enqueued_at),
  INDEX idx_fc_queue_expires (expires_at, status),
  INDEX idx_fc_queue_instance_flow (instance_id, flow_id)
);

-- ── ClawMind migration v16: node_hallucination_checks

CREATE TABLE IF NOT EXISTS node_hallucination_checks (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  attempt INTEGER NOT NULL DEFAULT 1 COMMENT '尝试次数',
  check_type VARCHAR(32) NOT NULL COMMENT '检查类型',
  severity VARCHAR(16) NOT NULL DEFAULT 'low' COMMENT '严重程度',
  passed INTEGER NOT NULL DEFAULT 1 COMMENT '是否通过(0否1是)',
  description TEXT COMMENT '检查描述',
  evidence TEXT COMMENT '检查证据',
  risk_score INTEGER NOT NULL DEFAULT 0 COMMENT '风险分数',
  risk_level VARCHAR(16) NOT NULL DEFAULT 'none' COMMENT '风险等级',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_nhc_flow_node (flow_id, node_id, attempt),
  INDEX idx_nhc_flow_id (flow_id)
);

-- ── ClawMind migration v21: execution_step_log

CREATE TABLE IF NOT EXISTS execution_step_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  step_type VARCHAR(32) NOT NULL COMMENT '步骤类型',
  timestamp BIGINT NOT NULL COMMENT '时间戳(unix秒)',
  input_summary TEXT COMMENT '输入摘要',
  output_summary TEXT COMMENT '输出摘要',
  llm_evaluation TEXT COMMENT 'LLM评估结果',
  decision_path TEXT COMMENT '决策路径',
  duration_ms BIGINT COMMENT '执行耗时(毫秒)',
  token_usage BIGINT COMMENT 'Token用量',
  metadata TEXT COMMENT '元数据JSON',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_esl_flow_id (flow_id),
  INDEX idx_esl_flow_node (flow_id, node_id),
  INDEX idx_esl_step_type (flow_id, step_type)
);

-- ── Migration v9: validation_templates (ClawWeb — task validation templates) ──

CREATE TABLE IF NOT EXISTS validation_templates (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  template_id VARCHAR(255) NOT NULL COMMENT '模板唯一标识(业务ID,用于YAML引用)',
  name VARCHAR(255) NOT NULL COMMENT '模板名称(显示用)',
  description TEXT COMMENT '模板描述',
  content MEDIUMTEXT NOT NULL COMMENT '模板内容(提示词/验证规则)',
  enabled INTEGER NOT NULL DEFAULT 1 COMMENT '是否启用(0禁用1启用)',
  category VARCHAR(64) COMMENT '任务类别(如complex/simple, 提取自content JSON)',
  grading_type VARCHAR(64) COMMENT '评分类型(如hybrid/automated/llm_judge, 提取自content JSON)',
  timeout_seconds INTEGER COMMENT '超时秒数(提取自content JSON)',
  grading_weights_json TEXT COMMENT '评分权重JSON(如{"automated":0.4,"llm_judge":0.6}, 提取自content JSON)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_validation_templates_template_id (template_id),
  INDEX idx_validation_templates_enabled (enabled),
  INDEX idx_validation_templates_category (category),
  INDEX idx_validation_templates_grading_type (grading_type)
);