CREATE TABLE IF NOT EXISTS `bcs_event_subscriptions` (
  `subscription_id` varchar(128) NOT NULL COMMENT '事件订阅唯一 ID',
  `name` varchar(128) NOT NULL COMMENT '事件订阅名称',
  `scope_type` varchar(32) NOT NULL COMMENT '订阅作用域类型，如 group',
  `scope_id` varchar(256) NOT NULL COMMENT '订阅作用域资源 ID',
  `status` varchar(32) NOT NULL COMMENT '订阅状态：pending、active、disabled 或 deleted',
  `current_revision` bigint(20) unsigned NOT NULL COMMENT '当前订阅配置版本号',
  `created_by_type` varchar(32) NOT NULL COMMENT '创建订阅的 Actor 类型',
  `created_by_id` varchar(256) NOT NULL COMMENT '创建订阅的 Actor ID',
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '订阅创建时间',
  `updated_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '订阅最后更新时间',
  `deleted_at` timestamp(3) NULL DEFAULT NULL COMMENT '订阅软删除时间',
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  PRIMARY KEY (`subscription_id`),
  KEY `idx_event_subscription_scope` (`env`, `scope_type`, `scope_id`, `status`),
  KEY `idx_event_subscription_status` (`env`, `status`, `updated_at`),
  KEY `idx_event_subscription_creator` (`env`, `created_by_type`, `created_by_id`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_subscription_revisions` (
  `subscription_id` varchar(128) NOT NULL COMMENT '所属事件订阅 ID',
  `revision` bigint(20) unsigned NOT NULL COMMENT '不可变订阅配置版本号',
  `event_filters_json` text NOT NULL COMMENT '规范化后的事件过滤器 JSON',
  `payload_mode` varchar(32) NOT NULL COMMENT 'Payload 投影模式：metadata_only 或 full',
  `endpoint_url` varchar(2048) NOT NULL COMMENT '校验后的完整 Webhook 回调地址',
  `request_timeout_ms` bigint(20) unsigned NOT NULL COMMENT '单次 Webhook 请求超时时间，单位毫秒',
  `activated_at` timestamp(3) NOT NULL COMMENT '该版本激活时间',
  `retired_at` timestamp(3) NULL DEFAULT NULL COMMENT '该版本停止生效时间',
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  PRIMARY KEY (`subscription_id`, `revision`),
  KEY `idx_event_revision_active` (`env`, `subscription_id`, `retired_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_scope_epochs` (
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  `scope_type` varchar(32) NOT NULL COMMENT '订阅作用域类型',
  `scope_id` varchar(256) NOT NULL COMMENT '订阅作用域资源 ID',
  `epoch` bigint(20) unsigned NOT NULL DEFAULT '0' COMMENT '作用域内订阅变更的单调递增版本号',
  `updated_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT 'Epoch 最后更新时间',
  PRIMARY KEY (`env`, `scope_type`, `scope_id`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_streams` (
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  `stream_key` varchar(384) NOT NULL COMMENT '事件顺序流唯一键',
  `last_sequence` bigint(20) unsigned NOT NULL DEFAULT '0' COMMENT '该流最后提交的事件序号',
  `updated_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '事件流最后更新时间',
  PRIMARY KEY (`env`, `stream_key`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_events` (
  `event_id` varchar(128) NOT NULL COMMENT '规范事件唯一 ID',
  `event_type` varchar(128) NOT NULL COMMENT '事件类型',
  `schema_version` varchar(32) NOT NULL COMMENT '事件数据 Schema 版本',
  `producer` varchar(64) NOT NULL COMMENT '事件生产者标识',
  `producer_key` varchar(256) NOT NULL COMMENT '生产者侧事件幂等键',
  `subject_type` varchar(64) NOT NULL COMMENT '事件直接关联资源类型',
  `subject_id` varchar(256) NOT NULL COMMENT '事件直接关联资源 ID',
  `group_id` varchar(256) DEFAULT NULL COMMENT '事件所属 Group ID',
  `session_id` varchar(256) DEFAULT NULL COMMENT '事件所属 Session ID',
  `task_id` varchar(256) DEFAULT NULL COMMENT '事件所属 Task ID',
  `run_id` varchar(256) DEFAULT NULL COMMENT '事件所属状态机 Run ID',
  `stream_key` varchar(384) NOT NULL COMMENT '事件顺序流唯一键',
  `sequence` bigint(20) unsigned NOT NULL COMMENT '事件在所属流中的单调递增序号',
  `actor_json` text DEFAULT NULL COMMENT '脱敏后的事件 Actor JSON',
  `correlation_id` varchar(256) DEFAULT NULL COMMENT '业务关联 ID',
  `causation_event_id` varchar(128) DEFAULT NULL COMMENT '直接前因事件 ID',
  `trace_id` varchar(256) DEFAULT NULL COMMENT '链路追踪 ID',
  `data_json` mediumtext NOT NULL COMMENT '规范事件 data JSON',
  `occurred_at` timestamp(3) NOT NULL COMMENT '业务事件发生时间',
  `recorded_at` timestamp(3) NOT NULL COMMENT '事件持久化时间',
  `fanout_status` varchar(32) NOT NULL COMMENT '事件分发状态：pending、completed 或 failed',
  `retention_until` timestamp(3) NOT NULL COMMENT '事件最早允许清理时间',
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  PRIMARY KEY (`event_id`),
  UNIQUE KEY `uk_event_producer` (`env`, `producer`, `producer_key`, `event_type`),
  UNIQUE KEY `uk_event_stream_sequence` (`env`, `stream_key`, `sequence`),
  KEY `idx_event_fanout_status` (`env`, `fanout_status`, `recorded_at`),
  KEY `idx_event_scope_type` (`env`, `group_id`(128), `session_id`(128), `event_type`, `recorded_at`),
  KEY `idx_event_causation` (`env`, `causation_event_id`),
  KEY `idx_event_retention` (`env`, `retention_until`, `fanout_status`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_fanout_targets` (
  `target_id` varchar(128) NOT NULL COMMENT '事件分发目标唯一 ID',
  `event_id` varchar(128) NOT NULL COMMENT '关联的规范事件 ID',
  `subscription_id` varchar(128) NOT NULL COMMENT '命中的事件订阅 ID',
  `subscription_revision` bigint(20) unsigned NOT NULL COMMENT '事件提交时固化的订阅版本号',
  `purpose` varchar(32) NOT NULL COMMENT '目标用途：normal、causal_prerequisite 或 manual_replay',
  `replay_request_id` varchar(128) NOT NULL DEFAULT '' COMMENT '人工重放请求幂等 ID，普通目标为空',
  `replay_of_delivery_id` varchar(128) DEFAULT NULL COMMENT '人工重放要替代的死信 Delivery ID',
  `depends_on_target_id` varchar(128) DEFAULT NULL COMMENT '跨流因果依赖的前置目标 ID',
  `status` varchar(32) NOT NULL COMMENT '目标状态：pending、materialized、cancelled 或 failed',
  `lease_owner` varchar(256) DEFAULT NULL COMMENT '当前领取目标的 Worker 租约标识',
  `lease_until` timestamp(3) NULL DEFAULT NULL COMMENT '目标领取租约到期时间',
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '目标创建时间',
  `materialized_at` timestamp(3) NULL DEFAULT NULL COMMENT '目标生成 Delivery 的时间',
  `cancelled_at` timestamp(3) NULL DEFAULT NULL COMMENT '目标取消时间',
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  PRIMARY KEY (`target_id`),
  UNIQUE KEY `uk_event_target_idempotency` (`env`, `subscription_id`, `subscription_revision`, `event_id`, `purpose`, `replay_request_id`),
  KEY `idx_event_target_pending` (`env`, `status`, `lease_until`, `created_at`),
  KEY `idx_event_target_dependency` (`env`, `depends_on_target_id`, `status`),
  KEY `idx_event_target_subscription` (`env`, `subscription_id`, `subscription_revision`, `status`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_deliveries` (
  `delivery_id` varchar(128) NOT NULL COMMENT 'Webhook 投递唯一 ID',
  `fanout_target_id` varchar(128) NOT NULL COMMENT '生成该投递的不可变分发目标 ID',
  `event_id` varchar(128) NOT NULL COMMENT '关联的规范事件 ID',
  `subscription_id` varchar(128) NOT NULL COMMENT '关联的事件订阅 ID',
  `subscription_revision` bigint(20) unsigned NOT NULL COMMENT '投递使用的固定订阅版本号',
  `stream_key` varchar(384) NOT NULL COMMENT '严格顺序投递通道键',
  `sequence` bigint(20) unsigned NOT NULL COMMENT '事件在所属流中的序号',
  `payload_bytes` mediumblob NOT NULL COMMENT '不可变的原始 HTTP 请求 Body',
  `payload_sha256` char(64) NOT NULL COMMENT '请求 Body 的 SHA-256 摘要',
  `status` varchar(32) NOT NULL COMMENT '投递状态：pending、in_flight、retry_wait、succeeded、dead_lettered、cancelled 或 skipped',
  `attempt_count` bigint(20) unsigned NOT NULL DEFAULT '0' COMMENT '已开始的投递尝试次数',
  `first_attempt_at` timestamp(3) NULL DEFAULT NULL COMMENT '首次投递尝试开始时间',
  `last_attempt_at` timestamp(3) NULL DEFAULT NULL COMMENT '最近一次投递尝试开始时间',
  `next_attempt_at` timestamp(3) NULL DEFAULT NULL COMMENT '下一次允许重试时间',
  `lease_owner` varchar(256) DEFAULT NULL COMMENT '当前领取投递的 Worker 租约标识',
  `lease_until` timestamp(3) NULL DEFAULT NULL COMMENT '投递领取租约到期时间',
  `last_http_status` int DEFAULT NULL COMMENT '最近一次 Webhook HTTP 状态码',
  `last_error_category` varchar(128) DEFAULT NULL COMMENT '最近一次脱敏错误分类',
  `last_error_summary` varchar(2048) DEFAULT NULL COMMENT '最近一次脱敏错误摘要',
  `dead_lettered_at` timestamp(3) NULL DEFAULT NULL COMMENT '投递进入死信状态的时间',
  `cancelled_at` timestamp(3) NULL DEFAULT NULL COMMENT '投递取消时间',
  `skipped_at` timestamp(3) NULL DEFAULT NULL COMMENT '管理员跳过投递的时间',
  `skip_actor` text DEFAULT NULL COMMENT '执行跳过操作的 Actor JSON',
  `skip_reason` varchar(128) DEFAULT NULL COMMENT '执行跳过操作的原因',
  `replay_of_delivery_id` varchar(128) DEFAULT NULL COMMENT '该重放投递所替代的死信 Delivery ID',
  `resolved_by_delivery_id` varchar(128) DEFAULT NULL COMMENT '成功解决该死信的重放 Delivery ID',
  `resolved_at` timestamp(3) NULL DEFAULT NULL COMMENT '死信被成功重放解决的时间',
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '投递创建时间',
  `succeeded_at` timestamp(3) NULL DEFAULT NULL COMMENT '投递成功时间',
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  PRIMARY KEY (`delivery_id`),
  UNIQUE KEY `uk_event_delivery_target` (`env`, `fanout_target_id`),
  KEY `idx_event_claim_due` (`env`, `status`, `next_attempt_at`, `lease_until`),
  KEY `idx_event_strict_lane` (`env`, `subscription_id`, `stream_key`, `status`, `sequence`),
  KEY `idx_event_delivery_subscription` (`env`, `subscription_id`, `status`, `created_at`),
  KEY `idx_event_delivery_replay` (`env`, `replay_of_delivery_id`, `status`),
  KEY `idx_event_delivery_retention` (`env`, `status`, `succeeded_at`, `dead_lettered_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_delivery_attempts` (
  `delivery_id` varchar(128) NOT NULL COMMENT '所属 Webhook 投递 ID',
  `attempt_no` bigint(20) unsigned NOT NULL COMMENT '投递尝试序号',
  `started_at` timestamp(3) NOT NULL COMMENT '尝试开始时间',
  `completed_at` timestamp(3) NULL DEFAULT NULL COMMENT '尝试完成时间',
  `latency_ms` bigint(20) unsigned DEFAULT NULL COMMENT '尝试耗时，单位毫秒',
  `result` varchar(32) DEFAULT NULL COMMENT '尝试结果：success、retryable 或 terminal',
  `http_status` int DEFAULT NULL COMMENT 'Webhook HTTP 状态码',
  `error_category` varchar(128) DEFAULT NULL COMMENT '脱敏错误分类',
  `error_summary` varchar(2048) DEFAULT NULL COMMENT '脱敏错误摘要',
  `response_bytes_observed` bigint(20) unsigned DEFAULT NULL COMMENT '观测到的响应 Body 字节数，不保存原文',
  `worker_id` varchar(256) NOT NULL COMMENT '执行该次尝试的 Worker ID',
  PRIMARY KEY (`delivery_id`, `attempt_no`),
  KEY `idx_event_attempt_result` (`result`, `started_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_subscription_audits` (
  `audit_id` varchar(128) NOT NULL COMMENT '订阅审计记录唯一 ID',
  `subscription_id` varchar(128) NOT NULL COMMENT '关联的事件订阅 ID',
  `revision` bigint(20) unsigned DEFAULT NULL COMMENT '关联的订阅配置版本号',
  `action` varchar(64) NOT NULL COMMENT '订阅管理操作类型',
  `actor_type` varchar(32) NOT NULL COMMENT '执行操作的 Actor 类型',
  `actor_id` varchar(256) NOT NULL COMMENT '执行操作的 Actor ID',
  `reason` varchar(128) DEFAULT NULL COMMENT '操作原因',
  `details_json` text DEFAULT NULL COMMENT '脱敏后的操作详情 JSON',
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '审计记录创建时间',
  `env` varchar(32) NOT NULL COMMENT '部署环境隔离标识',
  PRIMARY KEY (`audit_id`),
  KEY `idx_event_audit_subscription` (`env`, `subscription_id`, `created_at`),
  KEY `idx_event_audit_actor` (`env`, `actor_type`, `actor_id`, `created_at`)
) DEFAULT CHARSET = utf8mb4;
