-- ClawWeb/ClawFlow unified schema DDL — ZDAS
-- Generated from PROD database (agentclawdb) schema via odc-cli.
-- Compliance: BIGINT id, gmt_create/gmt_modified TIMESTAMP, no FLOAT/DOUBLE,
--             inline indexes for ZDAS compatibility, utf8mb4 charset.


-- Table: schema_version
CREATE TABLE IF NOT EXISTS schema_version (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `version` INT NOT NULL  COMMENT '版本号',
  `description` TEXT  COMMENT '版本描述',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间'
) DEFAULT CHARSET = utf8mb4;

-- Table: flow_events
CREATE TABLE IF NOT EXISTS flow_events (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `event_id` VARCHAR(255) NOT NULL  COMMENT '事件唯一标识',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `node_id` VARCHAR(255) DEFAULT NULL  COMMENT '节点ID',
  `event_type` VARCHAR(255) NOT NULL  COMMENT '事件类型',
  `attempt` INT DEFAULT NULL  COMMENT '重试次数',
  `time` INT NOT NULL  COMMENT '事件发生时间(unix秒)',
  `data_json` TEXT  COMMENT '事件数据JSON',
  `error_text` TEXT  COMMENT '错误信息',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_flow_events_flow_id` (`flow_id`),
KEY `idx_flow_events_workflow_id` (`workflow_id`),
KEY `idx_flow_events_time` (`time`)
) DEFAULT CHARSET = utf8mb4;

-- Table: flow_metrics
CREATE TABLE IF NOT EXISTS flow_metrics (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `node_id` VARCHAR(255) NOT NULL  COMMENT '节点ID',
  `metric_name` VARCHAR(255) NOT NULL  COMMENT '指标名称',
  `metric_value` DECIMAL(20,6) NOT NULL  COMMENT '指标值',
  `time` INT NOT NULL  COMMENT '指标采集时间(unix秒)',
  `labels_json` TEXT  COMMENT '标签JSON',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_flow_metrics_workflow` (`workflow_id`),
KEY `idx_flow_metrics_name` (`metric_name`),
KEY `idx_flow_metrics_time` (`time`)
) DEFAULT CHARSET = utf8mb4;

-- Table: triggered_alerts
CREATE TABLE IF NOT EXISTS triggered_alerts (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `node_id` VARCHAR(255) DEFAULT NULL  COMMENT '节点ID',
  `alert_rule` VARCHAR(255) NOT NULL  COMMENT '告警规则名称',
  `severity` VARCHAR(255) NOT NULL DEFAULT 'warning'  COMMENT '严重程度',
  `message` TEXT NOT NULL  COMMENT '告警消息',
  `time` INT NOT NULL  COMMENT '告警触发时间(unix秒)',
  `acknowledged` INT NOT NULL DEFAULT 0  COMMENT '是否已确认(0未确认1已确认)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_triggered_alerts_workflow` (`workflow_id`),
KEY `idx_triggered_alerts_ack` (`acknowledged`),
KEY `idx_triggered_alerts_time` (`time`)
) DEFAULT CHARSET = utf8mb4;

-- Table: node_executions
CREATE TABLE IF NOT EXISTS node_executions (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `node_id` VARCHAR(255) NOT NULL  COMMENT '节点ID',
  `executor_type` VARCHAR(255) DEFAULT NULL  COMMENT '执行器类型',
  `status` VARCHAR(255) NOT NULL  COMMENT '执行状态',
  `attempt` INT NOT NULL DEFAULT 1  COMMENT '尝试次数',
  `input_json` TEXT  COMMENT '输入数据JSON',
  `output_json` TEXT  COMMENT '输出数据JSON',
  `error_text` TEXT  COMMENT '错误信息',
  `duration_ms` BIGINT DEFAULT NULL,
  `token_usage_json` TEXT  COMMENT 'Token用量JSON',
  `started_at` BIGINT DEFAULT NULL,
  `completed_at` BIGINT DEFAULT NULL,
  `triggered_by` VARCHAR(255) DEFAULT NULL  COMMENT '触发来源节点ID',
  `node_title` VARCHAR(255) DEFAULT NULL  COMMENT '节点标题',
  `branch_id` VARCHAR(255) DEFAULT NULL  COMMENT '分支ID',
  `progress_message` TEXT  COMMENT '进度消息',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `session_key` VARCHAR(255) DEFAULT NULL,
  `session_id` VARCHAR(255) DEFAULT NULL,
  `system_context_json` TEXT,
  `embedded_session_key` VARCHAR(255) DEFAULT NULL  COMMENT 'embedded-agent节点的派生session key，用于关联Langf',
  `resolved_prompt` TEXT,
  `version` BIGINT DEFAULT 1  COMMENT '乐观锁版本号',
KEY `idx_node_exec_flow_id` (`flow_id`),
KEY `idx_node_exec_workflow_id` (`workflow_id`),
KEY `idx_node_exec_node_status` (`flow_id`, `node_id`, `status`),
KEY `idx_node_exec_created` (`gmt_create`)
) DEFAULT CHARSET = utf8mb4;

-- Table: flow_runs
CREATE TABLE IF NOT EXISTS flow_runs (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `workflow_title` VARCHAR(255) DEFAULT NULL  COMMENT '工作流标题',
  `status` VARCHAR(255) NOT NULL  COMMENT '运行状态',
  `params_json` TEXT  COMMENT '参数JSON',
  `input_json` TEXT  COMMENT '输入数据JSON',
  `result_json` TEXT  COMMENT '结果JSON',
  `node_count` BIGINT DEFAULT 0,
  `succeeded_count` BIGINT DEFAULT 0,
  `failed_count` BIGINT DEFAULT 0,
  `total_duration_ms` BIGINT DEFAULT NULL,
  `total_token_usage` BIGINT DEFAULT NULL,
  `triggered_by` VARCHAR(255) DEFAULT NULL  COMMENT '触发来源',
  `identity_key` TEXT  COMMENT '工作流身份键(用于分组)',
  `current_phase` VARCHAR(255) DEFAULT NULL  COMMENT '当前执行阶段',
  `started_at` BIGINT DEFAULT NULL,
  `completed_at` BIGINT DEFAULT NULL,
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `credentials_json` TEXT  COMMENT 'Parsed .credentials content (BOT_ID, OWN',
  `origin_session_key` VARCHAR(255) DEFAULT NULL  COMMENT 'SessionKey at workflow start (e.g. agent',
  `origin_session_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Resolved session UUID from sessionKey',
  `origin_bot_id` VARCHAR(255) DEFAULT NULL  COMMENT 'BaaS-format bot_id {BOT_ID}:{OWNER_ID} (',
  `user_id` VARCHAR(255) DEFAULT NULL  COMMENT '触发工作流的用户ID(senderId)，由get_current_user工具',
  `plugin_version` VARCHAR(255) DEFAULT NULL  COMMENT '插件版本号',
  `engine` VARCHAR(255) DEFAULT NULL  COMMENT '执行引擎标识(openclaw|claudecode|teclaw|hermes',
UNIQUE KEY `uk_flow_runs_flow_id` (`flow_id`),
KEY `idx_flow_runs_workflow_id` (`workflow_id`),
KEY `idx_flow_runs_status` (`status`),
KEY `idx_flow_runs_started` (`started_at`),
KEY `idx_flow_runs_user_id` (`user_id`),
KEY `idx_flow_runs_status_started` (`status`, `started_at`)
) DEFAULT CHARSET = utf8mb4;

-- Table: scheduled_triggers
CREATE TABLE IF NOT EXISTS scheduled_triggers (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `trigger_id` VARCHAR(255) NOT NULL  COMMENT '触发器ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `pack_id` VARCHAR(255) NOT NULL  COMMENT '包ID',
  `cron_expression` VARCHAR(255) NOT NULL  COMMENT 'Cron表达式',
  `timezone` VARCHAR(255) NOT NULL DEFAULT 'UTC'  COMMENT '时区',
  `params_json` TEXT  COMMENT '参数JSON',
  `max_concurrent` INT NOT NULL DEFAULT 1  COMMENT '最大并发数',
  `enabled` INT NOT NULL DEFAULT 1  COMMENT '是否启用(0禁用1启用)',
  `last_fire_time` INT DEFAULT NULL  COMMENT '上次触发时间(unix秒)',
  `next_fire_time` INT DEFAULT NULL  COMMENT '下次触发时间(unix秒)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_sched_triggers_workflow` (`workflow_id`),
KEY `idx_sched_triggers_enabled_next` (`enabled`, `next_fire_time`),
UNIQUE KEY `uk_sched_triggers_trigger_id` (`trigger_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: webhook_triggers
CREATE TABLE IF NOT EXISTS webhook_triggers (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `trigger_id` VARCHAR(255) NOT NULL  COMMENT '触发器ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `pack_id` VARCHAR(255) DEFAULT NULL  COMMENT '包ID',
  `secret` VARCHAR(255) DEFAULT NULL  COMMENT 'Webhook签名密钥',
  `payload_mapping` TEXT  COMMENT '请求体映射JSON',
  `allowed_ips` TEXT  COMMENT '允许的IP列表JSON',
  `enabled` INT NOT NULL DEFAULT 1  COMMENT '是否启用(0禁用1启用)',
  `description` TEXT  COMMENT '描述信息',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_webhook_triggers_workflow` (`workflow_id`),
UNIQUE KEY `uk_webhook_triggers_trigger_id` (`trigger_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: webhook_events
CREATE TABLE IF NOT EXISTS webhook_events (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `event_id` VARCHAR(255) NOT NULL  COMMENT '事件ID',
  `trigger_id` VARCHAR(255) NOT NULL  COMMENT '触发器ID',
  `flow_id` VARCHAR(255) DEFAULT NULL  COMMENT '流程实例ID',
  `status` VARCHAR(255) NOT NULL  COMMENT '处理状态',
  `request_method` VARCHAR(255) NOT NULL  COMMENT '请求方法',
  `request_headers` TEXT  COMMENT '请求头JSON',
  `request_body_hash` VARCHAR(255) DEFAULT NULL  COMMENT '请求体SHA256哈希',
  `response_code` INT DEFAULT NULL  COMMENT '响应状态码',
  `error_message` TEXT  COMMENT '错误信息',
  `ip_address` VARCHAR(255) DEFAULT NULL  COMMENT '来源IP地址',
  `event_type` VARCHAR(255) DEFAULT NULL,
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `payload_json` TEXT,
  `received_at` INT DEFAULT NULL,
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
KEY `idx_webhook_events_trigger` (`trigger_id`),
KEY `idx_webhook_events_event_id` (`event_id`),
KEY `idx_webhook_events_created` (`gmt_create`),
KEY `idx_webhook_events_dedup` (`event_id`, `gmt_create`)
) DEFAULT CHARSET = utf8mb4;

-- Table: workflow_specs
CREATE TABLE IF NOT EXISTS workflow_specs (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `pack_id` VARCHAR(255) DEFAULT NULL  COMMENT '包ID',
  `spec_json` TEXT NOT NULL  COMMENT '工作流规格JSON',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `title` VARCHAR(255) DEFAULT NULL  COMMENT '工作流标题，从spec_json提取，避免列表查询加载大字段',
  `version` VARCHAR(255) DEFAULT NULL  COMMENT '版本',
UNIQUE KEY `uk_workflow_specs_workflow_id` (`workflow_id`),
KEY `idx_wfs_workflow_version` (`workflow_id`, `version`)
) DEFAULT CHARSET = utf8mb4;

-- Table: knowledge_bases
CREATE TABLE IF NOT EXISTS knowledge_bases (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `kb_id` VARCHAR(255) NOT NULL  COMMENT '知识库唯一标识(业务ID,用于YAML引用)',
  `name` VARCHAR(255) NOT NULL  COMMENT '知识库名称(显示用)',
  `description` TEXT  COMMENT '知识库描述',
  `instance_name` VARCHAR(255) NOT NULL  COMMENT 'GRT实例名称',
  `interface_name` VARCHAR(255) NOT NULL  COMMENT 'GRT接口名称',
  `token` VARCHAR(255) NOT NULL  COMMENT 'GRT API Token',
  `user_name` VARCHAR(255) NOT NULL  COMMENT '调用者花名',
  `user_id` VARCHAR(255) NOT NULL  COMMENT '调用者工号',
  `top_k` INT NOT NULL DEFAULT 3  COMMENT '返回结果数量',
  `ranking_threshold` DECIMAL(20,6) NOT NULL DEFAULT '0.01'  COMMENT '精排置信度阈值',
  `vector_threshold` DECIMAL(20,6) NOT NULL DEFAULT '0.6'  COMMENT '向量相似度阈值',
  `ranking_model` VARCHAR(255) NOT NULL DEFAULT 'bge-reranker-base'  COMMENT '精排模型名称',
  `env` VARCHAR(255) NOT NULL DEFAULT 'prod'  COMMENT '环境(prod/pre)',
  `enabled` INT NOT NULL DEFAULT 1  COMMENT '是否启用(0禁用1启用)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_knowledge_bases_kb_id` (`kb_id`),
KEY `idx_knowledge_bases_enabled` (`enabled`),
KEY `idx_knowledge_bases_created` (`gmt_create`)
) DEFAULT CHARSET = utf8mb4;

-- Table: validation_templates
CREATE TABLE IF NOT EXISTS validation_templates (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `template_id` VARCHAR(255) NOT NULL  COMMENT '模板唯一标识(业务ID,用于YAML引用)',
  `name` VARCHAR(255) NOT NULL  COMMENT '模板名称(显示用)',
  `description` TEXT  COMMENT '模板描述',
  `content` TEXT NOT NULL  COMMENT '模板内容(提示词/验证规则)',
  `enabled` INT NOT NULL DEFAULT 1  COMMENT '是否启用(0禁用1启用)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `category` VARCHAR(255) DEFAULT NULL  COMMENT '任务类别(如complex/simple, 提取自content JSON)',
  `grading_type` VARCHAR(255) DEFAULT NULL  COMMENT '评分类型(如hybrid/automated/llm_judge, 提取自con',
  `timeout_seconds` INT DEFAULT NULL  COMMENT '超时秒数(提取自content JSON)',
  `grading_weights_json` TEXT  COMMENT '评分权重JSON(如{"automated":0.4,"llm_judge":0',
UNIQUE KEY `uk_validation_templates_template_id` (`template_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: facade_bindings
CREATE TABLE IF NOT EXISTS facade_bindings (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `command` VARCHAR(255) NOT NULL  COMMENT 'Slash命令(如/marketing-dispatch)',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `pack_id` VARCHAR(255) DEFAULT NULL  COMMENT '包ID',
  `remark` TEXT  COMMENT '备注',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_facade_bindings_command` (`command`),
KEY `idx_facade_bindings_workflow` (`workflow_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: approval_cards
CREATE TABLE IF NOT EXISTS approval_cards (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `node_id` VARCHAR(255) NOT NULL  COMMENT '审批节点ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `workflow_title` VARCHAR(255) DEFAULT NULL  COMMENT '工作流标题',
  `approval_type` VARCHAR(255) DEFAULT NULL  COMMENT '审批类型',
  `message` TEXT  COMMENT '审批消息',
  `card_fields_json` TEXT  COMMENT '审批卡片字段JSON',
  `approver_ids` TEXT NOT NULL  COMMENT '审批人工号列表(逗号分隔)',
  `approver_names` TEXT  COMMENT '审批人姓名列表(逗号分隔)',
  `approval_policy` VARCHAR(255) NOT NULL DEFAULT 'any'  COMMENT '审批策略(any/all/majority)',
  `approved_by` VARCHAR(255) NOT NULL  COMMENT '已同意人工号(逗号分隔)',
  `rejected_by` VARCHAR(255) NOT NULL  COMMENT '已驳回人工号(逗号分隔)',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '审批状态(pending/approved/rejected)',
  `delivery_mode` VARCHAR(255) NOT NULL DEFAULT 'card-web'  COMMENT '投递方式(card-web)',
  `created_at` BIGINT NOT NULL  COMMENT '创建时间(unix时间戳)',
  `resolved_at` BIGINT DEFAULT NULL  COMMENT '审批完成时间(unix时间戳)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '记录创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '记录修改时间',
  `comment` TEXT  COMMENT '审批备注',
KEY `idx_approval_cards_flow_node` (`flow_id`, `node_id`),
KEY `idx_approval_cards_status` (`status`),
KEY `idx_approval_cards_created` (`created_at`)
) DEFAULT CHARSET = utf8mb4;

-- Table: node_step_traces
CREATE TABLE IF NOT EXISTS node_step_traces (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `node_id` VARCHAR(255) NOT NULL  COMMENT '节点ID',
  `attempt` INT NOT NULL DEFAULT 1  COMMENT '执行次数',
  `step_seq` INT NOT NULL  COMMENT '步骤序号(1-based)',
  `step_type` VARCHAR(255) NOT NULL  COMMENT '步骤类型(tool_call/tool_result/assistant_tex',
  `skill_name` VARCHAR(255) DEFAULT NULL  COMMENT 'skill名称(无skill时为NULL)',
  `tool_name` VARCHAR(255) DEFAULT NULL  COMMENT '工具名称(tool_call/tool_result时有值)',
  `tool_use_id` VARCHAR(255) DEFAULT NULL  COMMENT '工具调用ID(关联tool_call↔tool_result)',
  `tool_input_json` TEXT  COMMENT '工具输入参数JSON(截断到2000字符)',
  `tool_output_text` TEXT  COMMENT '工具输出文本(截断到5000字符)',
  `is_error` INT NOT NULL DEFAULT 0  COMMENT '是否报错(0正常1报错)',
  `text_content` TEXT  COMMENT 'assistant_text类型输出(截断到5000字符)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `session_key` VARCHAR(255) DEFAULT NULL  COMMENT '节点的embedded session key，用于关联aw_langfuse_',
  `trace_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Langfuse trace唯一标识',
  `observation_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Langfuse observation唯一标识',
  `model` VARCHAR(255) DEFAULT NULL  COMMENT 'LLM模型名称（如GLM-5.1）',
  `latency_ms` BIGINT DEFAULT NULL,
  `prompt_tokens` BIGINT DEFAULT NULL,
  `completion_tokens` BIGINT DEFAULT NULL,
KEY `idx_nst_flow_node` (`flow_id`, `node_id`, `attempt`),
KEY `idx_nst_flow_id` (`flow_id`),
KEY `idx_nst_skill_name` (`skill_name`),
KEY `idx_nst_created` (`gmt_create`)
) DEFAULT CHARSET = utf8mb4;

-- Table: flow_control_slots
CREATE TABLE IF NOT EXISTS flow_control_slots (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `instance_id` VARCHAR(255) NOT NULL  COMMENT '实例标识(OWNER_ID_BOT_ID，如103892_20260402_mn',
  `scope_key` VARCHAR(255) NOT NULL  COMMENT '作用域键(如global、workflow:risk-review、execut',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `node_id` VARCHAR(255) DEFAULT NULL  COMMENT '节点ID(工作流/全局作用域为NULL，执行器作用域有值)',
  `acquired_at` BIGINT NOT NULL  COMMENT '获取时间(unix时间戳)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `session_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Gateway session ID that owns this slot,',
  `lease_expires_at` BIGINT NOT NULL DEFAULT 0  COMMENT '租约过期时间（Unix秒）。0=旧数据,>0=租约模式,过期后仅释放slot不修',
  `renew_count` INT NOT NULL DEFAULT 0  COMMENT '续租次数，每次heartbeat续租+1，仅用于监控调测',
UNIQUE KEY `uk_fc_slots_instance_scope_flow_node` (`instance_id`, `scope_key`, `flow_id`, `node_id`),
KEY `idx_fc_slots_instance_scope` (`instance_id`, `scope_key`)
) DEFAULT CHARSET = utf8mb4;

-- Table: flow_control_queue
CREATE TABLE IF NOT EXISTS flow_control_queue (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `instance_id` VARCHAR(255) NOT NULL  COMMENT '实例标识(OWNER_ID_BOT_ID，如103892_20260402_mn',
  `scope_key` VARCHAR(255) NOT NULL  COMMENT '作用域键(如global、workflow:risk-review、execut',
  `flow_id` VARCHAR(255) NOT NULL  COMMENT '流程实例ID',
  `node_id` VARCHAR(255) DEFAULT NULL  COMMENT '节点ID(工作流/全局作用域为NULL，执行器作用域有值)',
  `priority` INT NOT NULL DEFAULT 0  COMMENT '优先级(数值越小优先级越高)',
  `status` VARCHAR(255) NOT NULL DEFAULT 'queued'  COMMENT '状态(queued排队中|dispatched已派发|expired已过期)',
  `enqueued_at` BIGINT NOT NULL  COMMENT '入队时间(unix时间戳)',
  `dispatch_after` BIGINT DEFAULT NULL  COMMENT '最早可派发时间(unix时间戳，用于退避调度)',
  `expires_at` BIGINT DEFAULT NULL  COMMENT '过期时间(unix时间戳，队列超时)',
  `payload` TEXT  COMMENT '恢复执行所需的序列化上下文JSON',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_fc_queue_status` (`status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: cm_bench_domains
CREATE TABLE IF NOT EXISTS cm_bench_domains (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `domain_id` VARCHAR(255) NOT NULL  COMMENT 'Domain标识',
  `name` VARCHAR(255) NOT NULL  COMMENT 'Domain名称',
  `description` TEXT  COMMENT '描述',
  `created_by` VARCHAR(255) DEFAULT NULL  COMMENT '创建人',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT '域所有者用户ID',
  `status` VARCHAR(255) NOT NULL DEFAULT 'active'  COMMENT '状态: active/archived',
UNIQUE KEY `uk_cm_bench_domains_owner_domain` (`owner_user_id`, `domain_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: cm_bench_templates
CREATE TABLE IF NOT EXISTS cm_bench_templates (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `domain_id` VARCHAR(255) NOT NULL  COMMENT '所属Domain',
  `template_name` VARCHAR(255) NOT NULL  COMMENT '模板名称（domain内唯一）',
  `display_name` VARCHAR(255) DEFAULT NULL  COMMENT '展示名称',
  `description` TEXT  COMMENT '描述',
  `category` VARCHAR(255) DEFAULT NULL  COMMENT '分类',
  `target_type` VARCHAR(255) NOT NULL DEFAULT 'agent_session'  COMMENT '目标类型',
  `grading_type` VARCHAR(255) NOT NULL DEFAULT 'automated'  COMMENT '评分类型',
  `source` VARCHAR(255) NOT NULL DEFAULT 'agentbench'  COMMENT '来源',
  `source_path` VARCHAR(255) DEFAULT NULL  COMMENT '原始路径',
  `source_hash` VARCHAR(255) DEFAULT NULL  COMMENT '内容哈希',
  `latest_version` INT NOT NULL DEFAULT 1  COMMENT '最新版本号',
  `published_version` INT DEFAULT NULL  COMMENT '已发布版本号',
  `status` VARCHAR(255) NOT NULL DEFAULT 'draft'  COMMENT '状态: draft/published/archived',
  `created_by` VARCHAR(255) DEFAULT NULL  COMMENT '创建人',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `owner_user_id` VARCHAR(255) DEFAULT NULL  COMMENT '模板所有者用户ID',
UNIQUE KEY `uk_cm_bench_templates_owner_domain_name` (`owner_user_id`, `domain_id`, `template_name`),
KEY `idx_cm_bench_templates_domain_status` (`domain_id`, `status`),
KEY `idx_cm_bench_templates_domain_name` (`domain_id`, `template_name`)
) DEFAULT CHARSET = utf8mb4;

-- Table: cm_bench_template_versions
CREATE TABLE IF NOT EXISTS cm_bench_template_versions (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `domain_id` VARCHAR(255) NOT NULL  COMMENT '所属Domain',
  `template_name` VARCHAR(255) NOT NULL  COMMENT '模板名称',
  `version` INT NOT NULL  COMMENT '版本号',
  `display_name` VARCHAR(255) DEFAULT NULL  COMMENT '展示名称',
  `description` TEXT  COMMENT '描述',
  `content_md` TEXT NOT NULL  COMMENT 'Markdown内容',
  `parsed_meta_json` TEXT  COMMENT '解析后的元数据JSON',
  `source_path` VARCHAR(255) DEFAULT NULL  COMMENT '原始路径',
  `source_hash` VARCHAR(255) DEFAULT NULL  COMMENT '内容哈希',
  `status` VARCHAR(255) NOT NULL DEFAULT 'draft'  COMMENT '状态: draft/published/archived',
  `created_by` VARCHAR(255) DEFAULT NULL  COMMENT '创建人',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT '版本所有者用户ID',
UNIQUE KEY `uk_cm_btv_owner_dom_name_ver` (`owner_user_id`, `domain_id`, `template_name`, `version`),
KEY `idx_cm_bench_template_versions_domain_name_status` (`domain_id`, `template_name`, `status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: cm_bench_runs
CREATE TABLE IF NOT EXISTS cm_bench_runs (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `bench_run_id` VARCHAR(255) NOT NULL  COMMENT 'Run稳定ID',
  `domain_id` VARCHAR(255) NOT NULL  COMMENT '所属Domain',
  `template_name` VARCHAR(255) NOT NULL  COMMENT '模板名称',
  `template_version` INT NOT NULL  COMMENT '模板版本',
  `target_type` VARCHAR(255) NOT NULL DEFAULT 'agent_session'  COMMENT '目标类型',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '状态',
  `score` DECIMAL(20,6) DEFAULT NULL  COMMENT '得分',
  `max_score` DECIMAL(20,6) DEFAULT NULL  COMMENT '满分',
  `pass_rate` DECIMAL(20,6) DEFAULT NULL  COMMENT '通过率',
  `model` VARCHAR(255) DEFAULT NULL  COMMENT '模型',
  `suite` VARCHAR(255) DEFAULT NULL  COMMENT '套件',
  `scene` VARCHAR(255) DEFAULT NULL  COMMENT '场景',
  `triggered_by` VARCHAR(255) DEFAULT NULL  COMMENT '触发人',
  `clawmind_flow_id` VARCHAR(255) DEFAULT NULL  COMMENT 'ClawMind Flow ID',
  `session_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Session ID',
  `session_key` VARCHAR(255) DEFAULT NULL  COMMENT 'Session Key',
  `run_config_json` TEXT  COMMENT '运行配置JSON',
  `summary_json` TEXT  COMMENT '汇总JSON',
  `error_text` TEXT  COMMENT '错误信息',
  `started_at` BIGINT DEFAULT NULL  COMMENT '开始时间',
  `completed_at` BIGINT DEFAULT NULL  COMMENT '完成时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
  `owner_user_id` VARBINARY(255) NOT NULL  COMMENT 'Run所有者用户ID',
UNIQUE KEY `uk_cm_bench_runs_run_id` (`bench_run_id`),
KEY `idx_cm_bench_runs_domain_template` (`domain_id`, `template_name`),
KEY `idx_cm_bench_runs_status` (`status`),
KEY `idx_cm_bench_runs_clawmind_flow` (`clawmind_flow_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: cm_bench_task_results
CREATE TABLE IF NOT EXISTS cm_bench_task_results (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `result_id` VARCHAR(255) NOT NULL  COMMENT '结果ID',
  `bench_run_id` VARCHAR(255) NOT NULL  COMMENT 'Run ID',
  `task_id` VARCHAR(255) NOT NULL  COMMENT '任务ID',
  `task_name` VARCHAR(255) DEFAULT NULL  COMMENT '任务名称',
  `status` VARCHAR(255) NOT NULL  COMMENT '状态',
  `score` DECIMAL(20,6) DEFAULT NULL  COMMENT '得分',
  `max_score` DECIMAL(20,6) DEFAULT NULL  COMMENT '满分',
  `grading_type` VARCHAR(255) DEFAULT NULL  COMMENT '评分类型',
  `execution_time_ms` BIGINT DEFAULT NULL  COMMENT '执行耗时(ms)',
  `transcript_path` VARCHAR(255) DEFAULT NULL  COMMENT 'Transcript路径',
  `workspace_path` VARCHAR(255) DEFAULT NULL  COMMENT 'Workspace路径',
  `result_json` TEXT  COMMENT '结果JSON',
  `breakdown_json` TEXT  COMMENT '分解JSON',
  `notes` TEXT  COMMENT '备注',
  `error_text` TEXT  COMMENT '错误信息',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_cm_bench_task_results_result_id` (`result_id`),
KEY `idx_cm_bench_task_results_run` (`bench_run_id`),
KEY `idx_cm_bench_task_results_task` (`task_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: cm_bench_artifacts
CREATE TABLE IF NOT EXISTS cm_bench_artifacts (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `artifact_id` VARCHAR(255) NOT NULL  COMMENT 'Artifact稳定ID',
  `bench_run_id` VARCHAR(255) NOT NULL  COMMENT 'Run ID',
  `result_id` VARCHAR(255) DEFAULT NULL  COMMENT '任务结果ID',
  `task_id` VARCHAR(255) DEFAULT NULL  COMMENT '任务ID',
  `artifact_type` VARCHAR(255) NOT NULL  COMMENT 'Artifact类型: report/session/transcript/ag',
  `filename` VARCHAR(255) DEFAULT NULL  COMMENT '文件名',
  `content_type` VARCHAR(255) DEFAULT NULL  COMMENT '内容类型',
  `size_bytes` BIGINT DEFAULT NULL  COMMENT '内容大小',
  `storage_type` VARCHAR(255) NOT NULL DEFAULT 'db'  COMMENT '存储类型: db/path/object',
  `storage_path` VARCHAR(255) DEFAULT NULL  COMMENT '外部存储路径',
  `content_text` TEXT  COMMENT '文本内容',
  `content_json` TEXT  COMMENT 'JSON内容',
  `summary_json` TEXT  COMMENT '摘要JSON',
  `sha256` VARCHAR(255) DEFAULT NULL  COMMENT '内容SHA256',
  `created_by` VARCHAR(255) DEFAULT NULL  COMMENT '创建人',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT 'Artifact所有者用户ID',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_cm_bench_artifacts_artifact_id` (`artifact_id`),
KEY `idx_cm_bench_artifacts_run` (`bench_run_id`),
KEY `idx_cm_bench_artifacts_run_type` (`bench_run_id`, `artifact_type`),
KEY `idx_cm_bench_artifacts_task` (`bench_run_id`, `task_id`),
KEY `idx_cm_bench_artifacts_owner_run` (`owner_user_id`, `bench_run_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_workflow_templates
CREATE TABLE IF NOT EXISTS dev_workflow_templates (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `template_id` VARCHAR(255) NOT NULL  COMMENT '模板唯一标识',
  `name` VARCHAR(255) NOT NULL  COMMENT '模板名称',
  `description` TEXT  COMMENT '模板描述',
  `phases_json` TEXT NOT NULL  COMMENT '阶段定义JSON数组',
  `is_built_in` INT NOT NULL DEFAULT 0  COMMENT '是否内置模板 1=是 0=否',
  `created_by` VARCHAR(255) NOT NULL  COMMENT '创建人',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_dev_wf_templates_template_id` (`template_id`),
KEY `idx_dev_wf_templates_built_in` (`is_built_in`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_workflows
CREATE TABLE IF NOT EXISTS dev_workflows (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流实例唯一ID',
  `title` VARCHAR(255) NOT NULL  COMMENT '工作流标题',
  `template_id` VARCHAR(255) NOT NULL  COMMENT '关联模板ID',
  `dima_work_item_id` VARCHAR(255) NOT NULL  COMMENT 'Dima工作项ID',
  `dima_work_item_type` VARCHAR(255) NOT NULL  COMMENT 'Dima工作项类型 Req/Bug/Task',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '状态 pending/running/completed/cancelled/f',
  `current_phase` VARCHAR(255) DEFAULT NULL  COMMENT '当前阶段ID',
  `enabled_phases_json` TEXT NOT NULL  COMMENT '启用的阶段列表JSON',
  `config_json` TEXT  COMMENT '工作流配置JSON',
  `git_repo_url` VARCHAR(255) DEFAULT NULL  COMMENT 'Git仓库地址',
  `git_branch` VARCHAR(255) DEFAULT NULL  COMMENT 'Git分支',
  `pr_url` VARCHAR(255) DEFAULT NULL  COMMENT 'PR链接',
  `pr_id` VARCHAR(255) DEFAULT NULL  COMMENT 'PR ID',
  `timeout_hours` INT NOT NULL DEFAULT 72  COMMENT '超时时间(小时)',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT '负责人',
  `started_at` TIMESTAMP DEFAULT NULL  COMMENT '开始时间',
  `completed_at` TIMESTAMP DEFAULT NULL  COMMENT '完成时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_dev_workflows_workflow_id` (`workflow_id`),
KEY `idx_dev_workflows_status` (`status`),
KEY `idx_dev_workflows_dima_id` (`dima_work_item_id`),
KEY `idx_dev_workflows_template` (`template_id`),
KEY `idx_dev_workflows_owner` (`owner_user_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_workflow_phases
CREATE TABLE IF NOT EXISTS dev_workflow_phases (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '所属工作流ID',
  `phase_id` VARCHAR(255) NOT NULL  COMMENT '阶段ID',
  `phase_name` VARCHAR(255) NOT NULL  COMMENT '阶段名称',
  `phase_order` INT NOT NULL  COMMENT '阶段排序',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '状态 pending/running/waiting_confirm/confi',
  `enabled` INT NOT NULL DEFAULT 1  COMMENT '是否启用 1=是 0=否',
  `required` INT NOT NULL DEFAULT 1  COMMENT '是否必须 1=是 0=否',
  `has_human_gate` INT NOT NULL DEFAULT 0  COMMENT '是否有人工审批 1=是 0=否',
  `has_bot_execution` INT NOT NULL DEFAULT 1  COMMENT '是否有Bot执行 1=是 0=否',
  `bot_role` VARCHAR(255) DEFAULT NULL  COMMENT 'Bot角色',
  `default_timeout_minutes` INT NOT NULL DEFAULT 10  COMMENT '默认超时时间(分钟)',
  `document_url` VARCHAR(255) DEFAULT NULL  COMMENT '产出文档URL',
  `document_title` VARCHAR(255) DEFAULT NULL  COMMENT '产出文档标题',
  `result_summary` TEXT  COMMENT '执行结果摘要',
  `baas_run_id` VARCHAR(255) DEFAULT NULL  COMMENT 'BaaS运行ID',
  `error_message` TEXT  COMMENT '错误信息',
  `confirmed_by` VARCHAR(255) DEFAULT NULL  COMMENT '确认人',
  `confirm_comment` TEXT  COMMENT '确认备注',
  `rejected_by` VARCHAR(255) DEFAULT NULL  COMMENT '拒绝人',
  `reject_reason` TEXT  COMMENT '拒绝原因',
  `reject_to_phase_id` VARCHAR(255) DEFAULT NULL  COMMENT '拒绝回退到阶段ID',
  `version` INT NOT NULL DEFAULT 0  COMMENT '乐观锁版本号',
  `started_at` TIMESTAMP DEFAULT NULL  COMMENT '开始时间',
  `completed_at` TIMESTAMP DEFAULT NULL  COMMENT '完成时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_dev_wf_phases_wf_phase` (`workflow_id`, `phase_id`),
KEY `idx_dev_wf_phases_workflow` (`workflow_id`),
KEY `idx_dev_wf_phases_status` (`workflow_id`, `status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_phase_conversations
CREATE TABLE IF NOT EXISTS dev_phase_conversations (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `phase_id` VARCHAR(255) NOT NULL  COMMENT '阶段ID',
  `baas_message_id` VARCHAR(255) DEFAULT NULL  COMMENT 'BaaS消息唯一ID(幂等去重键)',
  `role` VARCHAR(255) NOT NULL  COMMENT '角色 user|assistant|system',
  `sender_id` VARCHAR(255) NOT NULL  COMMENT '发送者ID',
  `sender_name` VARCHAR(255) DEFAULT NULL  COMMENT '发送者显示名称',
  `content` TEXT NOT NULL  COMMENT '消息内容',
  `session_id` VARCHAR(255) DEFAULT NULL  COMMENT 'BaaS会话ID',
  `bot_id` VARCHAR(255) DEFAULT NULL  COMMENT 'BOT ID',
  `metadata_json` TEXT  COMMENT '元数据JSON(扩展字段)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_dev_phase_conv_workflow` (`workflow_id`),
KEY `idx_dev_phase_conv_wf_phase` (`workflow_id`, `phase_id`),
UNIQUE KEY `uk_dev_phase_conv_baas_msg` (`baas_message_id`),
KEY `idx_dev_phase_conv_session` (`session_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_approvals
CREATE TABLE IF NOT EXISTS dev_approvals (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `approval_id` VARCHAR(255) NOT NULL  COMMENT '审批单唯一ID(格式 apr-{uuid})',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `phase_id` VARCHAR(255) NOT NULL  COMMENT '阶段ID',
  `target_type` VARCHAR(255) NOT NULL  COMMENT '审批对象类型 artifact|code_change|phase',
  `target_id` VARCHAR(255) NOT NULL  COMMENT '审批对象ID',
  `title` VARCHAR(255) NOT NULL  COMMENT '审批标题',
  `description` TEXT  COMMENT '审批描述',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '状态 pending|approved|rejected|cancelled|e',
  `block_phase` INT NOT NULL DEFAULT 0  COMMENT '是否阻塞阶段 1=是 0=否',
  `version` INT NOT NULL DEFAULT 0  COMMENT '乐观锁版本号',
  `created_by` VARCHAR(255) NOT NULL  COMMENT '创建者ID',
  `resolved_at` TIMESTAMP DEFAULT NULL  COMMENT '审批解决时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_dev_approvals_approval_id` (`approval_id`),
KEY `idx_dev_approvals_workflow` (`workflow_id`),
KEY `idx_dev_approvals_wf_phase` (`workflow_id`, `phase_id`),
KEY `idx_dev_approvals_target` (`target_type`, `target_id`),
KEY `idx_dev_approvals_status` (`status`),
KEY `idx_dev_approvals_created_by` (`created_by`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_approval_reviewers
CREATE TABLE IF NOT EXISTS dev_approval_reviewers (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `approval_id` VARCHAR(255) NOT NULL  COMMENT '审批单ID(关联dev_approvals.approval_id)',
  `reviewer_id` VARCHAR(255) NOT NULL  COMMENT '审批人ID',
  `reviewer_name` VARCHAR(255) DEFAULT NULL  COMMENT '审批人显示名称',
  `notified` INT NOT NULL DEFAULT 0  COMMENT '是否已通知 1=是 0=否',
  `decision` VARCHAR(255) DEFAULT NULL  COMMENT '决策 approved|rejected|commented (NULL=未决策',
  `comment` TEXT  COMMENT '决策备注',
  `decided_at` TIMESTAMP DEFAULT NULL  COMMENT '决策时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_dev_approval_rev_approval_user` (`approval_id`, `reviewer_id`),
KEY `idx_dev_approval_rev_reviewer` (`reviewer_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_discussions
CREATE TABLE IF NOT EXISTS dev_discussions (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `discussion_id` VARCHAR(255) NOT NULL  COMMENT '讨论唯一ID(格式 disc-{uuid})',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `phase_id` VARCHAR(255) NOT NULL  COMMENT '阶段ID',
  `topic` VARCHAR(255) NOT NULL  COMMENT '讨论主题',
  `description` TEXT  COMMENT '讨论描述',
  `context_type` VARCHAR(255) DEFAULT NULL  COMMENT '关联上下文类型 artifact|code_change|phase',
  `context_id` VARCHAR(255) DEFAULT NULL  COMMENT '关联上下文ID',
  `status` VARCHAR(255) NOT NULL DEFAULT 'open'  COMMENT '状态 open|closed',
  `conclusion` TEXT  COMMENT '讨论结论(closed时填写)',
  `created_by` VARCHAR(255) NOT NULL  COMMENT '创建者ID',
  `closed_by` VARCHAR(255) DEFAULT NULL  COMMENT '关闭者ID',
  `closed_at` TIMESTAMP DEFAULT NULL  COMMENT '关闭时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `uk_dev_discussions_disc_id` (`discussion_id`),
KEY `idx_dev_discussions_workflow` (`workflow_id`),
KEY `idx_dev_discussions_wf_phase` (`workflow_id`, `phase_id`),
KEY `idx_dev_discussions_status` (`status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_discussion_replies
CREATE TABLE IF NOT EXISTS dev_discussion_replies (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `discussion_id` VARCHAR(255) NOT NULL  COMMENT '讨论ID(关联dev_discussions.discussion_id)',
  `author_id` VARCHAR(255) NOT NULL  COMMENT '回复者ID',
  `author_name` VARCHAR(255) DEFAULT NULL  COMMENT '回复者显示名称',
  `content` TEXT NOT NULL  COMMENT '回复内容',
  `parent_reply_id` BIGINT DEFAULT NULL  COMMENT '父回复ID(支持嵌套回复)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_dev_disc_rep_disc` (`discussion_id`),
KEY `idx_dev_disc_rep_author` (`author_id`),
KEY `idx_dev_disc_rep_parent` (`parent_reply_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_git_ops
CREATE TABLE IF NOT EXISTS dev_git_ops (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `phase_id` VARCHAR(255) NOT NULL  COMMENT '阶段ID',
  `operation` VARCHAR(255) NOT NULL  COMMENT 'Git操作类型 clone|pull|checkout|commit|push',
  `repo_url` VARCHAR(255) NOT NULL  COMMENT '仓库URL',
  `branch` VARCHAR(255) NOT NULL  COMMENT '分支名称',
  `commit_sha` VARCHAR(255) DEFAULT NULL  COMMENT '提交SHA',
  `commit_message` VARCHAR(255) DEFAULT NULL  COMMENT '提交消息',
  `remote_branch` VARCHAR(255) DEFAULT NULL  COMMENT '远程分支名称(可能与本地不同)',
  `summary` TEXT  COMMENT 'AI生成的变更摘要(原dev_code_changes.summary)',
  `result` VARCHAR(255) NOT NULL DEFAULT 'success'  COMMENT '执行结果 success|failed|timeout',
  `error_message` TEXT  COMMENT '错误信息(failed/timeout时)',
  `executed_by` VARCHAR(255) NOT NULL  COMMENT '执行者(通常是BOT ID)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_dev_git_ops_workflow` (`workflow_id`),
KEY `idx_dev_git_ops_wf_phase` (`workflow_id`, `phase_id`),
KEY `idx_dev_git_ops_commit` (`commit_sha`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_artifacts
CREATE TABLE IF NOT EXISTS dev_artifacts (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `phase_id` VARCHAR(255) NOT NULL  COMMENT '阶段ID',
  `artifact_type` VARCHAR(255) NOT NULL  COMMENT '产物类型 prd|architecture|tech_doc|api_spec|',
  `version` INT NOT NULL DEFAULT 1  COMMENT '版本号(同一wf+phase+type自动递增)',
  `title` VARCHAR(255) NOT NULL  COMMENT '产物标题',
  `content` TEXT  COMMENT '产物内容(内联存储)',
  `content_url` VARCHAR(255) DEFAULT NULL  COMMENT '产物外部链接(YuQue等)',
  `format` VARCHAR(255) NOT NULL DEFAULT 'markdown'  COMMENT '内容格式 markdown|yaml|json|html',
  `status` VARCHAR(255) NOT NULL DEFAULT 'current'  COMMENT '状态 draft|current|archived',
  `source` VARCHAR(255) NOT NULL DEFAULT 'bot'  COMMENT '来源 bot|human|imported',
  `authored_by` VARCHAR(255) NOT NULL  COMMENT '作者(通常是BOT ID或用户ID)',
  `archived_at` TIMESTAMP DEFAULT NULL  COMMENT '归档时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_dev_artifacts_workflow` (`workflow_id`),
KEY `idx_dev_artifacts_wf_phase` (`workflow_id`, `phase_id`),
KEY `idx_dev_artifacts_type_status` (`workflow_id`, `artifact_type`, `status`),
UNIQUE KEY `uk_dev_artifacts_wf_phase_type_ver` (`workflow_id`, `phase_id`, `artifact_type`, `version`)
) DEFAULT CHARSET = utf8mb4;

-- Table: dev_project_constraints
CREATE TABLE IF NOT EXISTS dev_project_constraints (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL  COMMENT '工作流ID',
  `version` INT NOT NULL DEFAULT 1  COMMENT '版本号(同一workflow自动递增)',
  `constraints_json` TEXT NOT NULL  COMMENT '约束内容JSON(技术栈/规范/限制等)',
  `change_summary` VARCHAR(255) DEFAULT NULL  COMMENT '本次变更摘要',
  `changed_by` VARCHAR(255) NOT NULL  COMMENT '变更者ID',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_dev_proj_constraints_workflow` (`workflow_id`),
UNIQUE KEY `uk_dev_proj_constraints_wf_ver` (`workflow_id`, `version`)
) DEFAULT CHARSET = utf8mb4;

-- Table: insight_failure_task
CREATE TABLE IF NOT EXISTS insight_failure_task (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `source_dt` CHAR NOT NULL  COMMENT '业务日期yyyyMMdd',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT 'Bot归属用户ID',
  `bot_id` VARCHAR(255) NOT NULL  COMMENT 'Bot ID',
  `bot_name` VARCHAR(255) NOT NULL  COMMENT 'Bot名称快照',
  `session_id` VARCHAR(255) NOT NULL  COMMENT 'Session ID',
  `task_index` INT NOT NULL DEFAULT 0  COMMENT 'Session内Task序号',
  `task_description` VARCHAR(255) NOT NULL  COMMENT 'Task描述快照',
  `is_complete` TINYINT NOT NULL DEFAULT 2  COMMENT 'LLM Judge完成状态：0未完成、1完成、2未知、3中止',
  `failure_class` VARCHAR(255) NOT NULL DEFAULT 'UNKNOWN'  COMMENT '失败分类',
  `judge_reason_summary` VARCHAR(255) DEFAULT NULL  COMMENT 'Judge原因摘要',
  `session_start_time` VARCHAR(255) DEFAULT NULL  COMMENT 'Session开始时间',
  `session_end_time` VARCHAR(255) DEFAULT NULL  COMMENT 'Session结束时间',
  `session_duration_seconds` INT DEFAULT NULL  COMMENT 'Session持续秒数',
  `is_cron` TINYINT NOT NULL DEFAULT 0  COMMENT '是否定时任务',
  `payload_ref` VARCHAR(255) NOT NULL  COMMENT '完整OSS Evidence URI，固定antsys-agentclaw-pr',
  `payload_etag` VARCHAR(255) NOT NULL  COMMENT 'Evidence对象ETag或内容校验值',
  `payload_version_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Evidence对象版本ID',
  `batch_id` VARCHAR(255) NOT NULL  COMMENT '写入批次ID',
  `data_as_of` VARCHAR(255) NOT NULL  COMMENT '数据水位',
  `judged_at` VARCHAR(255) DEFAULT NULL  COMMENT 'Judge时间',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_insight_failure_owner_dt` (`owner_user_id`, `source_dt`, `is_complete`),
KEY `idx_insight_failure_owner_bot_dt` (`owner_user_id`, `bot_id`, `source_dt`, `is_complete`),
KEY `idx_insight_failure_session_task` (`owner_user_id`, `session_id`, `task_index`)
) DEFAULT CHARSET = utf8mb4;

-- Table: insight_improvement_item
CREATE TABLE IF NOT EXISTS insight_improvement_item (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT '改进项处理用户',
  `bot_owner_user_id` VARCHAR(255) NOT NULL  COMMENT '目标Bot归属用户ID',
  `bot_id` VARCHAR(255) NOT NULL  COMMENT '目标Bot ID',
  `title` VARCHAR(255) NOT NULL  COMMENT '改进项标题',
  `user_guidance` TEXT  COMMENT '用户判断与重要改进方向',
  `source_type` VARCHAR(255) NOT NULL DEFAULT 'USER_SELECTED'  COMMENT 'USER_SELECTED、ADMIN_SELECTED或ADMIN_RULE',
  `source_rule_id` VARCHAR(255) DEFAULT NULL  COMMENT '管理员规则ID',
  `evidence_count` INT NOT NULL DEFAULT 0  COMMENT '冻结Task数量',
  `session_count` INT NOT NULL DEFAULT 0  COMMENT '涉及Session数量',
  `data_start_time` VARCHAR(255) DEFAULT NULL  COMMENT '证据开始时间',
  `data_end_time` VARCHAR(255) DEFAULT NULL  COMMENT '证据结束时间',
  `data_as_of` VARCHAR(255) NOT NULL  COMMENT '创建时数据水位',
  `batch_id` VARCHAR(255) NOT NULL  COMMENT '失败任务导入批次',
  `content_fingerprint` CHAR NOT NULL  COMMENT '证据集合与用户输入指纹',
  `idempotency_key` VARCHAR(255) NOT NULL  COMMENT '创建请求幂等键',
  `status` VARCHAR(255) NOT NULL DEFAULT 'ACTIVE'  COMMENT 'ACTIVE、IN_PROGRESS、RESOLVED或ARCHIVED',
  `applied_evolve_task_id` VARCHAR(255) DEFAULT NULL  COMMENT '成功应用本改进项的进化任务ID',
  `apply_request_id` VARCHAR(255) DEFAULT NULL  COMMENT 'Apply成功回写请求幂等键',
  `applied_by` VARCHAR(255) DEFAULT NULL  COMMENT '确认应用成功的用户或系统身份',
  `applied_at` TIMESTAMP DEFAULT NULL  COMMENT '应用成功时间',
  `version` INT NOT NULL DEFAULT 1  COMMENT '乐观锁版本',
  `created_by` VARCHAR(255) NOT NULL  COMMENT '创建人',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_insight_improvement_owner_bot` (`owner_user_id`, `bot_id`, `status`, `gmt_create`)
) DEFAULT CHARSET = utf8mb4;

-- Table: insight_improvement_evidence
CREATE TABLE IF NOT EXISTS insight_improvement_evidence (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `improvement_id` BIGINT NOT NULL  COMMENT '关联insight_improvement_item.id',
  `session_id` VARCHAR(255) NOT NULL  COMMENT 'Session ID',
  `task_index` INT NOT NULL DEFAULT 0  COMMENT 'Session内Task序号',
  `ordinal` INT NOT NULL DEFAULT 0  COMMENT '用户选择顺序',
  `task_description_snapshot` VARCHAR(255) NOT NULL  COMMENT 'Task描述快照',
  `failure_class_snapshot` VARCHAR(255) NOT NULL  COMMENT '失败分类快照',
  `reasoning_summary` VARCHAR(255) DEFAULT NULL  COMMENT 'Judge结论摘要',
  `payload_ref` VARCHAR(255) NOT NULL  COMMENT '完整OSS Evidence URI，固定antsys-agentclaw-pr',
  `payload_etag` VARCHAR(255) NOT NULL  COMMENT 'OSS对象ETag或内容校验值',
  `payload_version_id` VARCHAR(255) DEFAULT NULL  COMMENT 'OSS对象版本ID',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_insight_evidence_session_task` (`session_id`, `task_index`),
KEY `idx_insight_evidence_improvement_order` (`improvement_id`, `ordinal`)
) DEFAULT CHARSET = utf8mb4;

-- Table: insight_improvement_evolve_link
CREATE TABLE IF NOT EXISTS insight_improvement_evolve_link (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `improvement_id` BIGINT NOT NULL  COMMENT '关联insight_improvement_item.id',
  `evolve_task_id` VARCHAR(255) NOT NULL  COMMENT 'ce_tasks.task_id',
  `request_id` VARCHAR(255) NOT NULL  COMMENT '发起诊断请求幂等键',
  `created_by` VARCHAR(255) NOT NULL  COMMENT '发起用户',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_insight_evolve_task_id` (`evolve_task_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: insight_metric_daily
CREATE TABLE IF NOT EXISTS insight_metric_daily (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `source_dt` CHAR NOT NULL  COMMENT '业务日期yyyyMMdd',
  `owner_user_id` VARCHAR(255) NOT NULL  COMMENT 'Bot归属用户ID',
  `bot_id` VARCHAR(255) NOT NULL  COMMENT 'Bot ID',
  `bot_name` VARCHAR(255) NOT NULL  COMMENT 'Bot名称快照',
  `is_cron` TINYINT NOT NULL DEFAULT 0  COMMENT '是否定时任务',
  `total_task_count` DECIMAL(20,6) NOT NULL DEFAULT 0  COMMENT 'Task总数，可包含抽样反推小数',
  `valid_task_count` DECIMAL(20,6) NOT NULL DEFAULT 0  COMMENT '完成率分母Task数',
  `complete_task_count` DECIMAL(20,6) NOT NULL DEFAULT 0  COMMENT '已完成Task数',
  `capability_task_count` DECIMAL(20,6) NOT NULL DEFAULT 0  COMMENT 'Bot能力范围完成率分母',
  `capability_complete_task_count` DECIMAL(20,6) NOT NULL DEFAULT 0  COMMENT 'Bot能力范围内已完成Task数',
  `auto_complete_task_count` DECIMAL(20,6) NOT NULL DEFAULT 0  COMMENT '无需人工介入的已完成Task数',
  `failure_distribution_json` TEXT  COMMENT '失败分类到加权Task数的JSON对象',
  `batch_id` VARCHAR(255) NOT NULL  COMMENT '离线发布批次ID',
  `data_as_of` VARCHAR(255) NOT NULL  COMMENT '数据水位',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_insight_metric_owner_dt` (`owner_user_id`, `source_dt`, `is_cron`),
KEY `idx_insight_metric_owner_bot_dt` (`owner_user_id`, `bot_id`, `source_dt`, `is_cron`)
) DEFAULT CHARSET = utf8mb4;

-- Table: ce_task_sources
CREATE TABLE IF NOT EXISTS ce_task_sources (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `task_id` VARCHAR(255) NOT NULL  COMMENT 'Evolve Task ID',
  `source_type` VARCHAR(255) NOT NULL  COMMENT '输入来源类型',
  `source_id` VARCHAR(255) NOT NULL  COMMENT '来源对象ID',
  `source_schema_version` VARCHAR(255) NOT NULL  COMMENT '来源协议版本',
  `adapter_version` VARCHAR(255) DEFAULT NULL  COMMENT 'Adapter版本',
  `source_ref_json` TEXT NOT NULL  COMMENT '冻结的SourceRef',
  `source_digest` VARCHAR(255) DEFAULT NULL  COMMENT 'Plan Source内容摘要',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '解析状态',
  `error_code` VARCHAR(255) DEFAULT NULL  COMMENT '错误码',
  `error_message` TEXT  COMMENT '错误信息',
  `resolved_at` INT DEFAULT NULL  COMMENT '解析完成时间，Unix秒',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
KEY `idx_ce_task_sources_origin` (`source_type`, `source_id`),
KEY `idx_ce_task_sources_status` (`status`, `gmt_create`)
) DEFAULT CHARSET = utf8mb4;

-- Table: ce_repair_tool_calls
CREATE TABLE IF NOT EXISTS ce_repair_tool_calls (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `call_id` VARCHAR(255) NOT NULL  COMMENT '工具调用ID',
  `task_id` VARCHAR(255) NOT NULL  COMMENT 'CE Task ID',
  `step_id` VARCHAR(255) NOT NULL  COMMENT 'CE Step ID',
  `execution_id` VARCHAR(255) NOT NULL  COMMENT 'AIS execution ID',
  `authorization_scope_digest` VARCHAR(255) NOT NULL  COMMENT '用户Bot环境授权范围摘要',
  `client_request_id` VARCHAR(255) NOT NULL  COMMENT 'Agent稳定幂等键',
  `tool_name` VARCHAR(255) NOT NULL  COMMENT '类型化工具名',
  `operation` VARCHAR(255) NOT NULL  COMMENT '类型化操作名',
  `action_id` VARCHAR(255) DEFAULT NULL  COMMENT '获批Plan动作ID',
  `deadline_at` BIGINT DEFAULT NULL  COMMENT '调用截止时间Unix秒',
  `request_json` TEXT NOT NULL  COMMENT '脱敏且受限的执行请求或OSS引用',
  `is_write` INT NOT NULL DEFAULT 0  COMMENT '是否写操作',
  `status` VARCHAR(255) NOT NULL DEFAULT 'pending'  COMMENT '调用状态',
  `lease_owner` VARCHAR(255) DEFAULT NULL  COMMENT '当前执行租约持有者',
  `lease_expires_at` BIGINT DEFAULT NULL  COMMENT '执行租约截止时间Unix秒',
  `result_json` TEXT  COMMENT '脱敏且受限的终态信封或OSS引用',
  `result_digest` VARCHAR(255) DEFAULT NULL  COMMENT '完整终态信封摘要',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP  COMMENT '修改时间',
UNIQUE KEY `idx_ce_repair_tool_calls_call_id` (`call_id`),
UNIQUE KEY `idx_ce_repair_tool_calls_step_client` (`step_id`, `client_request_id`)
) DEFAULT CHARSET = utf8mb4;
