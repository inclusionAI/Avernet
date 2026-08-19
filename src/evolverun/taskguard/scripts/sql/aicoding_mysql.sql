-- ============================================================================
-- Auto-Coding (AI Coding) Platform — Full DDL for MySQL/ZDAS
-- Consolidated from v35–v41, covers 11 tables:
--
--   研发平台核心:
--     1. dev_workflow_templates   — 工作流模板定义（内置+自定义）
--     2. dev_workflows            — 工作流实例状态机
--     3. dev_workflow_phases      — 阶段执行追踪（乐观锁）
--     4. dima_work_items          — Dima 工作项本地缓存
--     5. log_analysis_results     — LLM 日志错误分析
--     6. pending_operations       — 外部写失败重试队列
--
--   引擎子系统:
--     7. flow_metrics             — 节点指标时序数据
--     8. triggered_alerts         — 告警记录
--     9. scheduled_triggers       — Cron 定时调度
--    10. webhook_triggers         — Webhook 触发端点
--    11. webhook_events           — Webhook 请求日志
-- ============================================================================


-- ═══════════════════════════════════════════════════════════════════════
-- 1. dev_workflow_templates
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_workflow_templates (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  template_id VARCHAR(255) NOT NULL COMMENT '模板唯一标识',
  name VARCHAR(255) NOT NULL COMMENT '模板名称',
  description TEXT COMMENT '模板描述',
  phases_json MEDIUMTEXT NOT NULL COMMENT '阶段定义JSON数组',
  is_built_in INTEGER NOT NULL DEFAULT 0 COMMENT '是否内置模板 1=是 0=否',
  created_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '创建人',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_dev_wf_templates_template_id (template_id),
  INDEX idx_dev_wf_templates_built_in (is_built_in)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 2. dev_workflows
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_workflows (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流实例唯一ID',
  title VARCHAR(255) NOT NULL COMMENT '工作流标题',
  template_id VARCHAR(255) NOT NULL COMMENT '关联模板ID',
  dima_work_item_id VARCHAR(255) NOT NULL COMMENT 'Dima工作项ID',
  dima_work_item_type VARCHAR(32) NOT NULL COMMENT 'Dima工作项类型 Req/Bug/Task',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态 pending/running/completed/cancelled/failed',
  current_phase VARCHAR(64) COMMENT '当前阶段ID',
  enabled_phases_json TEXT NOT NULL COMMENT '启用的阶段列表JSON',
  config_json TEXT COMMENT '工作流配置JSON',
  git_repo_url VARCHAR(1024) COMMENT 'Git仓库地址',
  git_branch VARCHAR(512) COMMENT 'Git分支',
  pr_url VARCHAR(1024) COMMENT 'PR链接',
  pr_id VARCHAR(255) COMMENT 'PR ID',
  timeout_hours INTEGER NOT NULL DEFAULT 72 COMMENT '超时时间(小时)',
  owner_user_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '负责人',
  started_at TIMESTAMP NULL COMMENT '开始时间',
  completed_at TIMESTAMP NULL COMMENT '完成时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_dev_workflows_workflow_id (workflow_id),
  INDEX idx_dev_workflows_status (status),
  INDEX idx_dev_workflows_dima_id (dima_work_item_id),
  INDEX idx_dev_workflows_template (template_id),
  INDEX idx_dev_workflows_owner (owner_user_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 3. dev_workflow_phases
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_workflow_phases (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '所属工作流ID',
  phase_id VARCHAR(64) NOT NULL COMMENT '阶段ID',
  phase_name VARCHAR(255) NOT NULL COMMENT '阶段名称',
  phase_order INTEGER NOT NULL COMMENT '阶段排序',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态 pending/running/waiting_confirm/confirmed/rejected/skipped/failed',
  enabled INTEGER NOT NULL DEFAULT 1 COMMENT '是否启用 1=是 0=否',
  required INTEGER NOT NULL DEFAULT 1 COMMENT '是否必须 1=是 0=否',
  has_human_gate INTEGER NOT NULL DEFAULT 0 COMMENT '是否有人工审批 1=是 0=否',
  has_bot_execution INTEGER NOT NULL DEFAULT 1 COMMENT '是否有Bot执行 1=是 0=否',
  bot_role VARCHAR(128) COMMENT 'Bot角色',
  default_timeout_minutes INTEGER NOT NULL DEFAULT 10 COMMENT '默认超时时间(分钟)',
  document_url VARCHAR(2048) COMMENT '产出文档URL',
  document_title VARCHAR(512) COMMENT '产出文档标题',
  result_summary MEDIUMTEXT COMMENT '执行结果摘要',
  baas_run_id VARCHAR(255) COMMENT 'BaaS运行ID',
  error_message TEXT COMMENT '错误信息',
  confirmed_by VARCHAR(128) COMMENT '确认人',
  confirm_comment TEXT COMMENT '确认备注',
  rejected_by VARCHAR(128) COMMENT '拒绝人',
  reject_reason TEXT COMMENT '拒绝原因',
  reject_to_phase_id VARCHAR(64) COMMENT '拒绝回退到阶段ID',
  version INTEGER NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  bot_id VARCHAR(128) COMMENT '绑定的Bot ID，用于阶段调度',
  prompt_template TEXT COMMENT 'Prompt模板，支持{{变量}}插值',
  prompt_variables_json TEXT COMMENT 'Prompt变量声明JSON [{name, description, defaultValue}]',
  approvers_json TEXT COMMENT '审批人列表JSON [{empId, name, role}]',
  approval_policy VARCHAR(32) DEFAULT 'any' COMMENT '审批策略 any|all|majority',
  prompt_resolved TEXT COMMENT '运行时解析后的完整Prompt',
  gate_position VARCHAR(16) DEFAULT 'post-bot' COMMENT '审批关卡位置 pre-bot|post-bot',
  confirmed_by_json TEXT COMMENT '已确认人记录JSON [{empId, name, confirmedAt}]',
  started_at TIMESTAMP NULL COMMENT '开始时间',
  completed_at TIMESTAMP NULL COMMENT '完成时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_dev_wf_phases_wf_phase (workflow_id, phase_id),
  INDEX idx_dev_wf_phases_workflow (workflow_id),
  INDEX idx_dev_wf_phases_status (workflow_id, status)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 4. dima_work_items
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dima_work_items (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  dima_id VARCHAR(255) NOT NULL COMMENT 'Dima工作项ID',
  dima_type VARCHAR(32) NOT NULL COMMENT 'Dima工作项类型 Req/Bug/Task',
  subject VARCHAR(512) NOT NULL COMMENT '工作项标题',
  content MEDIUMTEXT COMMENT '工作项内容',
  status VARCHAR(64) COMMENT '工作项状态',
  priority VARCHAR(32) COMMENT '优先级 urgent/high/medium/low',
  processor VARCHAR(255) COMMENT '处理人',
  creator VARCHAR(255) COMMENT '创建人',
  project_id VARCHAR(64) COMMENT '项目ID',
  workspace_id VARCHAR(64) COMMENT '空间ID',
  tenant_id VARCHAR(64) COMMENT '租户ID',
  linked_workflow_id VARCHAR(255) COMMENT '关联工作流ID',
  dima_gmt_create TIMESTAMP NULL COMMENT 'Dima创建时间',
  dima_gmt_modified TIMESTAMP NULL COMMENT 'Dima修改时间',
  synced_at TIMESTAMP NOT NULL COMMENT '同步时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_dima_work_items_dima_id (dima_id),
  INDEX idx_dima_work_items_type (dima_type),
  INDEX idx_dima_work_items_status (status),
  INDEX idx_dima_work_items_synced (synced_at),
  INDEX idx_dima_work_items_linked (linked_workflow_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 5. log_analysis_results
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS log_analysis_results (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  analysis_id VARCHAR(255) NOT NULL COMMENT '分析结果唯一ID',
  error_pattern VARCHAR(1024) NOT NULL COMMENT '错误模式/关键词',
  error_count INTEGER NOT NULL DEFAULT 1 COMMENT '错误出现次数',
  root_cause TEXT COMMENT '根因分析',
  severity VARCHAR(16) NOT NULL DEFAULT 'medium' COMMENT '严重程度 critical/high/medium/low',
  fix_suggestion TEXT COMMENT '修复建议',
  estimated_changed_files INTEGER COMMENT '预估涉及文件数',
  is_known_issue INTEGER NOT NULL DEFAULT 0 COMMENT '是否已知问题 1=是 0=否',
  related_bug_id VARCHAR(255) COMMENT '关联Bug ID',
  dima_bug_id VARCHAR(255) COMMENT 'Dima Bug ID',
  linked_workflow_id VARCHAR(255) COMMENT '关联工作流ID',
  status VARCHAR(32) NOT NULL DEFAULT 'new' COMMENT '状态 new/analyzed/bug_created/workflow_triggered/ignored',
  log_source VARCHAR(255) COMMENT '日志来源',
  lookback_minutes INTEGER NOT NULL DEFAULT 30 COMMENT '回看时间窗口(分钟)',
  analysis_json MEDIUMTEXT COMMENT '完整分析结果JSON',
  cooldown_until TIMESTAMP NULL COMMENT '冷却截止时间(避免重复分析)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_log_analysis_analysis_id (analysis_id),
  INDEX idx_log_analysis_status (status),
  INDEX idx_log_analysis_severity (severity),
  INDEX idx_log_analysis_pattern (error_pattern),
  INDEX idx_log_analysis_cooldown (cooldown_until)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 6. pending_operations
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS pending_operations (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  op_id VARCHAR(255) NOT NULL COMMENT '操作唯一ID',
  op_type VARCHAR(64) NOT NULL COMMENT '操作类型 dima_create/yuque_post/etc',
  target_id VARCHAR(255) NOT NULL COMMENT '目标资源ID',
  payload_json MEDIUMTEXT NOT NULL COMMENT '操作负载JSON',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态 pending/processing/done/failed',
  attempt_count INTEGER NOT NULL DEFAULT 0 COMMENT '已重试次数',
  max_attempts INTEGER NOT NULL DEFAULT 3 COMMENT '最大重试次数',
  next_retry_at TIMESTAMP NULL COMMENT '下次重试时间',
  last_error TEXT COMMENT '最近一次错误信息',
  workflow_id VARCHAR(255) COMMENT '关联工作流ID',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_pending_ops_op_id (op_id),
  INDEX idx_pending_ops_status (status, next_retry_at),
  INDEX idx_pending_ops_workflow (workflow_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 7. flow_metrics
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS flow_metrics (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) NOT NULL COMMENT '节点ID',
  metric_name VARCHAR(255) NOT NULL COMMENT '指标名称',
  metric_value DECIMAL(20,6) NOT NULL COMMENT '指标值',
  `time` BIGINT NOT NULL COMMENT '采集时间戳',
  labels_json MEDIUMTEXT COMMENT '标签JSON',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_flow_metrics_workflow (workflow_id),
  INDEX idx_flow_metrics_name (metric_name),
  INDEX idx_flow_metrics_time (`time`)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 8. triggered_alerts
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS triggered_alerts (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  flow_id VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  node_id VARCHAR(255) COMMENT '节点ID',
  alert_rule VARCHAR(255) NOT NULL COMMENT '告警规则名称',
  severity VARCHAR(255) NOT NULL DEFAULT 'warning' COMMENT '严重级别 warning/critical/fatal',
  message TEXT NOT NULL COMMENT '告警消息',
  `time` BIGINT NOT NULL COMMENT '告警时间戳',
  acknowledged TINYINT NOT NULL DEFAULT 0 COMMENT '是否已确认 0/1',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_triggered_alerts_workflow (workflow_id),
  INDEX idx_triggered_alerts_ack (acknowledged),
  INDEX idx_triggered_alerts_time (`time`)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 9. scheduled_triggers
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS scheduled_triggers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  trigger_id VARCHAR(255) NOT NULL COMMENT '触发器唯一ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  pack_id VARCHAR(255) NOT NULL COMMENT '所属Pack ID',
  cron_expression VARCHAR(255) NOT NULL COMMENT 'Cron表达式',
  timezone VARCHAR(255) NOT NULL DEFAULT 'UTC' COMMENT '时区',
  params_json MEDIUMTEXT COMMENT '工作流启动参数JSON',
  max_concurrent INTEGER NOT NULL DEFAULT 1 COMMENT '最大并发执行数',
  enabled TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用 0/1',
  last_fire_time TIMESTAMP NULL COMMENT '上次触发时间',
  next_fire_time TIMESTAMP NULL COMMENT '下次触发时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_sched_triggers_workflow (workflow_id),
  INDEX idx_sched_triggers_enabled_next (enabled, next_fire_time),
  UNIQUE INDEX uk_sched_triggers_trigger_id (trigger_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 10. webhook_triggers
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS webhook_triggers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  trigger_id VARCHAR(255) NOT NULL COMMENT '触发器唯一ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  pack_id VARCHAR(255) COMMENT '所属Pack ID',
  secret VARCHAR(255) COMMENT 'Webhook签名密钥',
  payload_mapping MEDIUMTEXT COMMENT '请求体到工作流参数的映射JSON',
  allowed_ips MEDIUMTEXT COMMENT '允许的IP白名单JSON',
  enabled TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用 0/1',
  description TEXT COMMENT '描述',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_webhook_triggers_workflow (workflow_id),
  UNIQUE INDEX uk_webhook_triggers_trigger_id (trigger_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 11. webhook_events
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS webhook_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  event_id VARCHAR(255) NOT NULL COMMENT '事件唯一ID',
  trigger_id VARCHAR(255) NOT NULL COMMENT '关联触发器ID',
  flow_id VARCHAR(255) COMMENT '触发的流程实例ID',
  status VARCHAR(255) NOT NULL COMMENT '处理状态 pending/processing/done/failed',
  request_method VARCHAR(255) NOT NULL COMMENT 'HTTP方法',
  request_headers MEDIUMTEXT COMMENT '请求头JSON',
  request_body_hash VARCHAR(255) COMMENT '请求体SHA256哈希',
  response_code INTEGER COMMENT 'HTTP响应码',
  error_message TEXT COMMENT '错误信息',
  ip_address VARCHAR(255) COMMENT '来源IP地址',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_webhook_events_trigger (trigger_id),
  INDEX idx_webhook_events_event_id (event_id),
  INDEX idx_webhook_events_created (gmt_create),
  INDEX idx_webhook_events_dedup (event_id, gmt_create)
);