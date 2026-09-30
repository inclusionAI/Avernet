-- Evolvetrace minimal MySQL schema (core tables only).
-- Generated from evolvetrace_mysql.sql.

CREATE TABLE IF NOT EXISTS schema_version (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `version` INT NOT NULL COMMENT '版本号',
  `description` TEXT COMMENT '版本描述',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间'
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS flow_events (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `event_id` VARCHAR(255) NOT NULL COMMENT '事件唯一标识',
  `flow_id` VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `node_id` VARCHAR(255) DEFAULT NULL COMMENT '节点ID',
  `event_type` VARCHAR(255) NOT NULL COMMENT '事件类型',
  `attempt` INT DEFAULT NULL COMMENT '重试次数',
  `time` INT NOT NULL COMMENT '事件发生时间(unix秒)',
  `data_json` TEXT COMMENT '事件数据JSON',
  `error_text` TEXT COMMENT '错误信息',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  KEY `idx_flow_events_flow_id` (`flow_id`),
  KEY `idx_flow_events_workflow_id` (`workflow_id`),
  KEY `idx_flow_events_time` (`time`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS flow_metrics (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `node_id` VARCHAR(255) NOT NULL COMMENT '节点ID',
  `metric_name` VARCHAR(255) NOT NULL COMMENT '指标名称',
  `metric_value` DECIMAL(20,6) NOT NULL COMMENT '指标值',
  `time` INT NOT NULL COMMENT '指标采集时间(unix秒)',
  `labels_json` TEXT COMMENT '标签JSON',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  KEY `idx_flow_metrics_workflow` (`workflow_id`),
  KEY `idx_flow_metrics_name` (`metric_name`),
  KEY `idx_flow_metrics_time` (`time`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS triggered_alerts (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `node_id` VARCHAR(255) DEFAULT NULL COMMENT '节点ID',
  `alert_rule` VARCHAR(255) NOT NULL COMMENT '告警规则名称',
  `severity` VARCHAR(255) NOT NULL DEFAULT 'warning' COMMENT '严重程度',
  `message` TEXT NOT NULL COMMENT '告警消息',
  `time` INT NOT NULL COMMENT '告警触发时间(unix秒)',
  `acknowledged` INT NOT NULL DEFAULT 0 COMMENT '是否已确认(0未确认1已确认)',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  KEY `idx_triggered_alerts_workflow` (`workflow_id`),
  KEY `idx_triggered_alerts_ack` (`acknowledged`),
  KEY `idx_triggered_alerts_time` (`time`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS node_executions (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `node_id` VARCHAR(255) NOT NULL COMMENT '节点ID',
  `executor_type` VARCHAR(255) DEFAULT NULL COMMENT '执行器类型',
  `status` VARCHAR(255) NOT NULL COMMENT '执行状态',
  `attempt` INT NOT NULL DEFAULT 1 COMMENT '尝试次数',
  `input_json` TEXT COMMENT '输入数据JSON',
  `output_json` TEXT COMMENT '输出数据JSON',
  `error_text` TEXT COMMENT '错误信息',
  `duration_ms` BIGINT DEFAULT NULL,
  `token_usage_json` TEXT COMMENT 'Token用量JSON',
  `started_at` BIGINT DEFAULT NULL,
  `completed_at` BIGINT DEFAULT NULL,
  `triggered_by` VARCHAR(255) DEFAULT NULL COMMENT '触发来源节点ID',
  `node_title` VARCHAR(255) DEFAULT NULL COMMENT '节点标题',
  `branch_id` VARCHAR(255) DEFAULT NULL COMMENT '分支ID',
  `progress_message` TEXT COMMENT '进度消息',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `session_key` VARCHAR(255) DEFAULT NULL,
  `session_id` VARCHAR(255) DEFAULT NULL,
  `system_context_json` TEXT,
  `embedded_session_key` VARCHAR(255) DEFAULT NULL COMMENT 'embedded-agent节点的派生session key，用于关联Langf',
  `resolved_prompt` TEXT,
  `version` BIGINT DEFAULT 1 COMMENT '乐观锁版本号',
  KEY `idx_node_exec_flow_id` (`flow_id`),
  KEY `idx_node_exec_workflow_id` (`workflow_id`),
  KEY `idx_node_exec_node_status` (`flow_id`, `node_id`, `status`),
  KEY `idx_node_exec_created` (`gmt_create`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS flow_runs (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `flow_id` VARCHAR(255) NOT NULL COMMENT '流程实例ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `workflow_title` VARCHAR(255) DEFAULT NULL COMMENT '工作流标题',
  `status` VARCHAR(255) NOT NULL COMMENT '运行状态',
  `params_json` TEXT COMMENT '参数JSON',
  `input_json` TEXT COMMENT '输入数据JSON',
  `result_json` TEXT COMMENT '结果JSON',
  `node_count` BIGINT DEFAULT 0,
  `succeeded_count` BIGINT DEFAULT 0,
  `failed_count` BIGINT DEFAULT 0,
  `total_duration_ms` BIGINT DEFAULT NULL,
  `total_token_usage` BIGINT DEFAULT NULL,
  `triggered_by` VARCHAR(255) DEFAULT NULL COMMENT '触发来源',
  `identity_key` TEXT COMMENT '工作流身份键(用于分组)',
  `current_phase` VARCHAR(255) DEFAULT NULL COMMENT '当前执行阶段',
  `started_at` BIGINT DEFAULT NULL,
  `completed_at` BIGINT DEFAULT NULL,
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `credentials_json` TEXT COMMENT 'Parsed .credentials content (BOT_ID, OWN',
  `origin_session_key` VARCHAR(255) DEFAULT NULL COMMENT 'SessionKey at workflow start (e.g. agent',
  `origin_session_id` VARCHAR(255) DEFAULT NULL COMMENT 'Resolved session UUID from sessionKey',
  `origin_bot_id` VARCHAR(255) DEFAULT NULL COMMENT 'BaaS-format bot_id {BOT_ID}:{OWNER_ID} (',
  `user_id` VARCHAR(255) DEFAULT NULL COMMENT '触发工作流的用户ID(senderId)，由get_current_user工具',
  `plugin_version` VARCHAR(255) DEFAULT NULL COMMENT '插件版本号',
  `engine` VARCHAR(255) DEFAULT NULL COMMENT '执行引擎标识(openclaw|claudecode|teclaw|hermes',
  UNIQUE KEY `uk_flow_runs_flow_id` (`flow_id`),
  KEY `idx_flow_runs_workflow_id` (`workflow_id`),
  KEY `idx_flow_runs_status` (`status`),
  KEY `idx_flow_runs_started` (`started_at`),
  KEY `idx_flow_runs_user_id` (`user_id`),
  KEY `idx_flow_runs_status_started` (`status`, `started_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_specs (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `pack_id` VARCHAR(255) DEFAULT NULL COMMENT '包ID',
  `spec_json` TEXT NOT NULL COMMENT '工作流规格JSON',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `title` VARCHAR(255) DEFAULT NULL COMMENT '工作流标题，从spec_json提取，避免列表查询加载大字段',
  `version` VARCHAR(255) DEFAULT NULL COMMENT '版本',
  UNIQUE KEY `uk_workflow_specs_workflow_id` (`workflow_id`),
  KEY `idx_wfs_workflow_version` (`workflow_id`, `version`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS facade_bindings (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `command` VARCHAR(255) NOT NULL COMMENT 'Slash命令(如/marketing-dispatch)',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `pack_id` VARCHAR(255) DEFAULT NULL COMMENT '包ID',
  `remark` TEXT COMMENT '备注',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE KEY `uk_facade_bindings_command` (`command`),
  KEY `idx_facade_bindings_workflow` (`workflow_id`)
) DEFAULT CHARSET = utf8mb4;

-- Not present in upstream evolvetrace_mysql.sql but required by BotWorkflowPermissionRepository.
CREATE TABLE IF NOT EXISTS bot_workflow_permissions (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `bot_id` VARCHAR(255) DEFAULT NULL COMMENT 'Bot ID',
  `bot_owner_id` VARCHAR(255) NOT NULL COMMENT 'Bot归属用户ID',
  `workflow_id` VARCHAR(255) NOT NULL COMMENT '工作流ID',
  `env` VARCHAR(255) NOT NULL COMMENT '环境标识',
  `can_view` INT NOT NULL DEFAULT 0 COMMENT '是否可查看',
  `can_execute` INT NOT NULL DEFAULT 0 COMMENT '是否可执行',
  `can_edit` INT NOT NULL DEFAULT 0 COMMENT '是否可编辑',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  KEY `idx_bot_wf_perm_workflow` (`workflow_id`),
  KEY `idx_bot_wf_perm_owner` (`bot_owner_id`, `workflow_id`),
  KEY `idx_bot_wf_perm_bot` (`bot_id`, `bot_owner_id`, `workflow_id`)
) DEFAULT CHARSET = utf8mb4;
