-- ============================================================
-- Run Archive 功能 DDL
-- 包含 run_logs 新建表 + aw_langfuse_traces/aw_langfuse_observation 确认建表
-- 数据库: MySQL / OceanBase (生产环境)
-- ============================================================

-- ── 1. run_logs 表（新建） ──
-- 存储 console.log/warn/error 拦截捕获的日志，按 flow_id 关联工作流实例

CREATE TABLE IF NOT EXISTS `run_logs` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  `flow_id` VARCHAR(255) NOT NULL COMMENT '工作流实例ID',
  `node_id` VARCHAR(255) DEFAULT NULL COMMENT '节点ID（从console消息中正则提取）',
  `level` VARCHAR(32) NOT NULL COMMENT '日志级别: log / warn / error',
  `source` VARCHAR(255) DEFAULT NULL COMMENT '来源标签: controller / embedded-agent 等',
  `message` TEXT NOT NULL COMMENT '日志消息全文',
  `timestamp` BIGINT NOT NULL COMMENT '日志时间戳（毫秒）',
  `seq` INT NOT NULL COMMENT '同一flowId内的递增序号',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modify` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  INDEX `idx_run_logs_flow_id` (`flow_id`),
  INDEX `idx_run_logs_flow_node` (`flow_id`, `node_id`),
  INDEX `idx_run_logs_level` (`flow_id`, `level`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Console日志捕获表 — 工作流运行档案';


-- ── 2. aw_langfuse_traces 表（确认存在） ──
-- 运行档案通过 node_executions.embedded_session_key → session_id 关联查询此表
-- 此表通常由 clawweb 的 Langfuse ingestion 流程自动创建，此处仅作确认

CREATE TABLE IF NOT EXISTS `aw_langfuse_traces` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modify` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `trace_id` VARCHAR(64) DEFAULT NULL COMMENT 'Trace唯一标识',
  `gmt_trace` BIGINT DEFAULT NULL COMMENT 'Trace时间戳(毫秒)',
  `name` VARCHAR(256) DEFAULT NULL COMMENT 'Trace名称',
  `input` LONGTEXT DEFAULT NULL COMMENT '输入数据(JSON)',
  `output` LONGTEXT DEFAULT NULL COMMENT '输出数据(JSON)',
  `session_id` VARCHAR(1024) DEFAULT NULL COMMENT '会话ID(= embedded_session_key)',
  `release_version` VARCHAR(128) DEFAULT NULL COMMENT '发布版本',
  `version` VARCHAR(128) DEFAULT NULL COMMENT '版本号',
  `user_id` VARCHAR(128) DEFAULT NULL COMMENT '用户ID',
  `metadata` LONGTEXT DEFAULT NULL COMMENT '元数据(JSON)',
  `tags` VARCHAR(512) DEFAULT NULL COMMENT '标签(逗号分隔)',
  `is_public` TINYINT(1) DEFAULT '0' COMMENT '是否公开',
  `environment` VARCHAR(64) DEFAULT NULL COMMENT '环境标识',
  `html_path` VARCHAR(512) DEFAULT NULL COMMENT 'HTML路径',
  `latency` DECIMAL(10,3) DEFAULT NULL COMMENT '延迟(毫秒)',
  `total_cost` DECIMAL(10,6) DEFAULT NULL COMMENT '总成本',
  `observations` VARCHAR(4096) DEFAULT NULL COMMENT 'observation的id列表',
  `scores` VARCHAR(64) DEFAULT NULL COMMENT '分数',
  `additional_properties` LONGTEXT DEFAULT NULL COMMENT '额外参数',
  `is_updated` TINYINT(4) DEFAULT '0' COMMENT '是否已刷新',
  `ak_prefix` VARCHAR(32) DEFAULT NULL COMMENT 'AK前缀',
  `business` VARCHAR(128) DEFAULT NULL COMMENT '业务标记',
  `bot_id` VARCHAR(256) DEFAULT NULL COMMENT 'bot_id',
  `device_id` VARCHAR(256) DEFAULT NULL COMMENT '设备ID',
  `real_session_id` VARCHAR(1024) DEFAULT NULL COMMENT '真实session_id',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trace_id` (`trace_id`),
  KEY `idx_gmt_trace` (`gmt_trace`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_session_id` (`session_id`(255)),
  KEY `idx_name` (`name`),
  KEY `idx_gmt_create` (`gmt_create`),
  KEY `idx_gmt_modify` (`gmt_modify`),
  KEY `idx_gmt_create_update` (`is_updated`, `gmt_create`),
  KEY `idx_ak` (`ak_prefix`),
  KEY `idx_business` (`business`),
  KEY `idx_device_id` (`device_id`),
  KEY `idx_bot_id` (`bot_id`),
  KEY `idx_real_session_id` (`real_session_id`(255))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Langfuse Trace表';


-- ── 3. aw_langfuse_observation 表（确认存在） ──
-- 通过 trace_id 关联 aw_langfuse_traces，存储 LLM 调用的工具过程和输入输出

CREATE TABLE IF NOT EXISTS `aw_langfuse_observation` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modify` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '修改时间',
  `observation_id` VARCHAR(64) DEFAULT NULL COMMENT 'Observation唯一标识',
  `trace_id` VARCHAR(64) DEFAULT NULL COMMENT '关联的Trace ID',
  `parent_observation_id` VARCHAR(64) DEFAULT NULL COMMENT '父Observation ID',
  `type` VARCHAR(32) DEFAULT NULL COMMENT '类型: SPAN / GENERATION / EVENT',
  `name` VARCHAR(255) DEFAULT NULL COMMENT 'Observation名称',
  `start_time` BIGINT DEFAULT NULL COMMENT '开始时间(毫秒)',
  `end_time` BIGINT DEFAULT NULL COMMENT '结束时间(毫秒)',
  `completion_start_time` BIGINT DEFAULT NULL COMMENT '完成开始时间(毫秒)',
  `model` VARCHAR(255) DEFAULT NULL COMMENT '模型标识',
  `model_id` VARCHAR(255) DEFAULT NULL COMMENT '模型ID',
  `model_parameters` LONGTEXT DEFAULT NULL COMMENT '模型参数(JSON)',
  `prompt_id` VARCHAR(64) DEFAULT NULL COMMENT 'Prompt ID',
  `prompt_name` VARCHAR(255) DEFAULT NULL COMMENT 'Prompt名称',
  `prompt_version` INT DEFAULT NULL COMMENT 'Prompt版本',
  `input` LONGTEXT DEFAULT NULL COMMENT '输入数据(JSON, 含system prompt, user message)',
  `output` LONGTEXT DEFAULT NULL COMMENT '输出数据(JSON, 含LLM回复, tool calls)',
  `metadata` LONGTEXT DEFAULT NULL COMMENT '元数据(JSON)',
  `version` VARCHAR(64) DEFAULT NULL COMMENT '版本号',
  `level` VARCHAR(32) DEFAULT NULL COMMENT '级别: DEBUG / INFO / WARNING / ERROR',
  `status_message` TEXT DEFAULT NULL COMMENT '状态消息(错误时含错误信息)',
  `usage_input_tokens` INT DEFAULT NULL COMMENT '输入token数',
  `usage_output_tokens` INT DEFAULT NULL COMMENT '输出token数',
  `usage_total_tokens` INT DEFAULT NULL COMMENT '总token数',
  `usage_units` VARCHAR(64) DEFAULT NULL COMMENT '计量单位',
  `usage_cost` DECIMAL(20,10) DEFAULT NULL COMMENT '使用成本',
  `usage_cost_per_unit` DECIMAL(20,10) DEFAULT NULL COMMENT '单位成本',
  `usage_details` LONGTEXT DEFAULT NULL COMMENT '详细使用量(JSON)',
  `cost_details` LONGTEXT DEFAULT NULL COMMENT '成本明细(JSON)',
  `input_price` DECIMAL(20,10) DEFAULT NULL COMMENT '输入价格',
  `output_price` DECIMAL(20,10) DEFAULT NULL COMMENT '输出价格',
  `total_price` DECIMAL(20,10) DEFAULT NULL COMMENT '总价格',
  `calculated_input_cost` DECIMAL(20,10) DEFAULT NULL COMMENT '计算输入成本',
  `calculated_output_cost` DECIMAL(20,10) DEFAULT NULL COMMENT '计算输出成本',
  `calculated_total_cost` DECIMAL(20,10) DEFAULT NULL COMMENT '计算总成本',
  `latency` DECIMAL(20,10) DEFAULT NULL COMMENT '延迟(秒)',
  `time_to_first_token` DECIMAL(20,10) DEFAULT NULL COMMENT '首token时间(秒)',
  `environment` VARCHAR(64) DEFAULT NULL COMMENT '环境标识',
  `additional_properties` LONGTEXT DEFAULT NULL COMMENT '额外属性(JSON)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_observation_id` (`observation_id`),
  KEY `idx_trace_id` (`trace_id`),
  KEY `idx_type` (`type`),
  KEY `idx_start_time` (`start_time`),
  KEY `idx_name` (`name`),
  KEY `idx_model` (`model`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Langfuse Observation表';


-- ── 4. 关联关系说明 ──
-- 运行档案查询路径:
--
--   flow_runs.flow_id
--     → node_executions.flow_id (获取节点执行记录)
--       → node_executions.embedded_session_key (获取Langfuse关联键)
--         → aw_langfuse_traces.session_id (查询LLM trace)
--           → aw_langfuse_observation.trace_id (查询工具调用详情)
--
--   flow_runs.flow_id
--     → run_logs.flow_id (查询console日志)
--     → flow_events.flow_id (查询事件时间线)
--     → node_step_traces.flow_id (查询步骤追踪)
--     → execution_step_log.flow_id (查询执行步骤日志)
