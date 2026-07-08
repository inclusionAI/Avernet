-- BaaS 核心表：租户、Bot、设备模板、设备、Bot设备关系
-- 参见: docs/baas_table_design.md

-- 租户管理表。多租户隔离的主数据。无 env/domain（跨环境共享）。无 UUID，不支持版本化。
CREATE TABLE `baas_tenant` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `name` varchar(256) NOT NULL COMMENT '租户名称',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `extra_config` mediumtext DEFAULT NULL COMMENT '扩展配置',
  `env` varchar(16) NOT NULL COMMENT 'Env',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_name_env` (`name`, `env`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='租户表';

-- 设备模板配置。使用 template_uuid 进行多版本追踪。无 env/domain（租户内跨环境共享）。
CREATE TABLE `baas_device_template` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `template_id` bigint(20) NOT NULL COMMENT '资源模板的唯一编号，会被用作paas设备id的编码中,全局唯一',
  `template_uuid` varchar(128) NOT NULL COMMENT '多版本追踪 UUID',
  `tenant` varchar(64) NOT NULL COMMENT '租户name',
  `type` varchar(32) NOT NULL COMMENT '资源Paas类型，例如ARCA或SIGMA等',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `status` varchar(32) NOT NULL DEFAULT 'CREATED' COMMENT 'CREATED | AUDITED | ONLINE | OFFLINE',
  `name` varchar(64) NOT NULL COMMENT '模板名称: openclaw, moltis, etc.',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `config` mediumtext DEFAULT NULL COMMENT '设备配置 (endpoint, model, capabilities, etc.)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_template_status` (`tenant`, `template_uuid`, `status`, `is_deleted`),
  UNIQUE KEY `uk_template_id` (`template_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备模板表';

-- BaaS bot 表。使用 bot_uuid 进行多版本追踪和状态生命周期管理。
CREATE TABLE `baas_bot` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `bot_uuid` varchar(128) NOT NULL COMMENT '多版本追踪 UUID',
  `tenant` varchar(64) NOT NULL COMMENT '租户名称/baas_tenant.name',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT 0 COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING | ACTIVE | FAILED | RELEASED',
  `name` varchar(256) NOT NULL COMMENT 'Bot 名称',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `template_uuid` varchar(128) DEFAULT NULL COMMENT 'FK to baas_device_template.template_uuid (不可变引用)',
  `replica_desired` int(11) NOT NULL DEFAULT 1 COMMENT '目标实例数',
  `replica_minimum` int(11) NOT NULL DEFAULT 1 COMMENT '最小实例数',
  `replica_maximum` int(11) NOT NULL DEFAULT 10 COMMENT '最大实例数',
  `auto_scaling_enabled` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否启用自动伸缩',
  `sla_grade` varchar(32) NOT NULL DEFAULT 'standard' COMMENT 'SLA 等级: standard | premium | enterprise',
  `extra_config` mediumtext COMMENT '扩展配置',
  PRIMARY KEY(`id`),
  UNIQUE KEY `uk_tenant_bot_status` (`tenant`, `bot_uuid`, `status`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 表';

-- 设备表。使用 device_uuid 进行多版本追踪。Bot 与设备关系通过 baas_bot_device_rel 追踪。
CREATE TABLE `baas_device` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `device_uuid` varchar(128) NOT NULL COMMENT '设备唯一 UUID',
  `tenant` varchar(64) NOT NULL COMMENT '租户 ID',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING | ACTIVE | RELEASED | FAILED',
  `provider_type` varchar(32) DEFAULT NULL COMMENT '设备供应方: local | daas | arca',
  `provider_device_id` varchar(128) DEFAULT NULL COMMENT '设备供应方设备 ID',
  `provider_device_props` mediumtext DEFAULT NULL COMMENT '设备属性 (callback_token, client_id, sandbox_id, nas_mappings等)',
  `extra_config` mediumtext DEFAULT NULL COMMENT '扩展配置',
  `err_msg` text DEFAULT NULL COMMENT '错误信息，若有',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_device_status` (`tenant`, `device_uuid`, `status`, `is_deleted`) BLOCK_SIZE 16384 LOCAL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备表';

-- Bot 与设备关系表。一个设备同一时间只能绑定到一个 bot。
CREATE TABLE `baas_bot_device_rel` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户名称/baas_tenant.name',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT 0 COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id',
  `device_uuid` varchar(128) NOT NULL COMMENT 'FK to baas_device.device_uuid (不可变引用)',
  PRIMARY KEY(`id`),
  UNIQUE KEY `uk_tenant_bot_device` (`tenant`, `bot_id`, `device_uuid`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 设备关系表';

-- 发布生命周期表。目标为 bot。
CREATE TABLE `baas_publish` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户名称/baas_tenant.name',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT 0 COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id',
  `publish_type` varchar(32) NOT NULL DEFAULT 'CREATE' COMMENT 'CREATE | SCALE_UP | SCALE_DOWN | RESTART | UPDATE',
  `name` varchar(256) NOT NULL COMMENT '发布名称',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `publisher` varchar(32) NOT NULL COMMENT '发布人 (工号:花名)',
  `replica_desired` int(11) NOT NULL DEFAULT 1 COMMENT '目标设备数',
  `batch_capacity` int(11) NOT NULL DEFAULT 1 COMMENT '每批设备数',
  `batch_number` int(11) DEFAULT NULL COMMENT '滚动重启批次数量',
  `cooldown_seconds` int(11) NOT NULL DEFAULT 30 COMMENT '冷却等待秒数',
  `config_version` varchar(64) DEFAULT NULL COMMENT '配置版本号',
  `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING | ACTIVE | FAILED | RELEASE | APPROVING | REJECTED | REVOKED | SUCCESS',
  `last_publish_id` bigint(20) unsigned DEFAULT NULL COMMENT '上次成功发布 ID',
  `changelog` varchar(4096) DEFAULT NULL COMMENT '变更说明',
  `extra_config` mediumtext COMMENT '扩展配置',
  PRIMARY KEY(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 发布表';

-- 发布批次追踪表。用于滚动发布/重启。每次发布创建多个批次，按顺序处理。
CREATE TABLE `baas_publish_batch` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户名称/baas_tenant.name',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT 0 COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `publish_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_publish.id',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id (反范式化)',
  `batch_index` int(11) NOT NULL COMMENT '批次号 (1, 2, 3...)',
  `batch_capacity` int(11) NOT NULL COMMENT '本批次设备数',
  `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING | RUNNING | COMPLETED | FAILED | ROLLED_BACK',
  `gmt_start` timestamp NULL DEFAULT NULL COMMENT '批次开始时间',
  `gmt_complete` timestamp NULL DEFAULT NULL COMMENT '批次完成时间',
  `error_message` varchar(2000) DEFAULT NULL COMMENT '错误信息',
  `extra_config` mediumtext COMMENT '扩展配置',
  PRIMARY KEY(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 发布批次表';

-- 伸缩和生命周期事件的审计日志表。
CREATE TABLE `baas_publish_record` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户名称/baas_tenant.name',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT 0 COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `device_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_device.id',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id (反范式化)',
  `publish_id` bigint(20) unsigned DEFAULT NULL COMMENT 'FK to baas_publish.id',
  `batch_id` bigint(20) unsigned DEFAULT NULL COMMENT 'FK to baas_publish_batch.id',
  `event_type` varchar(32) NOT NULL COMMENT 'SCALE_UP | SCALE_DOWN | START | STOP | RESTART',
  `trigger_source` varchar(32) DEFAULT 'manual' COMMENT 'manual | auto | api | system',
  `publish_reason` varchar(1000) DEFAULT NULL COMMENT '原因',
  `result_status` varchar(32) DEFAULT 'SUCCESS' COMMENT 'CREATED | SUCCESS | FAILED',
  `result_message` varchar(4096) DEFAULT NULL COMMENT '结果信息',
  `extra_config` mediumtext COMMENT '扩展配置',
  PRIMARY KEY(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 发布记录表';

CREATE TABLE `baas_bot_session` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `bot_uuid` varchar(128) NOT NULL COMMENT 'bot标识',
  `invoker` varchar(256) NOT NULL COMMENT '请求来源方',
  `session_id` varchar(256) NOT NULL COMMENT '会话id',
  `req` mediumtext DEFAULT NULL COMMENT '请求信息',
  `result` mediumtext DEFAULT NULL COMMENT '结果信息',
  `context` mediumtext DEFAULT NULL COMMENT '上下文信息',
  `status` varchar(32) NOT NULL COMMENT '会话状态',
  `device_uuid` varchar(128) NOT NULL COMMENT '实际承接会话的device',
  `env` varchar(16) NOT NULL COMMENT '环境，开发测试或预发/线上',
  `err_msg` text DEFAULT NULL COMMENT '错误详情，若有',
  `tenant` varchar(64) NOT NULL COMMENT '租户',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_session_id` (`tenant`, `session_id`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_bot_dev_ivk_time` (`bot_uuid`, `device_uuid`, `invoker`, `gmt_create`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_time_bot` (`gmt_create`, `bot_uuid`) BLOCK_SIZE 16384 GLOBAL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT = 'bot请求会话表，记录针对bot的业务调用';

CREATE TABLE `baas_local_user_machine` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `template_id` bigint(20) NOT NULL COMMENT 'baas_device_template的template_id唯一性字段',
  `user_id` varchar(128) NOT NULL COMMENT '用户标识',
  `machine_id` varchar(128) NOT NULL COMMENT '本地设备平台标识，如用户的某台mac电脑',
  `machine_info` text DEFAULT NULL COMMENT '本地设备平台其它信息',
  `last_heartbeat` timestamp NOT NULL COMMENT '本地设备平台最后一次心跳时间',
  `connected_server_instance` varchar(512) NOT NULL COMMENT '本地设备平台当前保持连接的后端管理拼台的节点标识',
  `status` varchar(64) NOT NULL COMMENT '本地设备平台状态：ONLINE / OFFLINE / DISABLED',
  `env` varchar(16) NOT NULL COMMENT '部署环境',
  `connected_route_info` varchar(1024) DEFAULT NULL COMMENT '调用路由信息',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_machine_env` (`machine_id`, `env`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_user_env` (`user_id`, `env`) BLOCK_SIZE 16384 GLOBAL
) COMMENT = '用户、本地bot平台设备节点关系表';

-- Bot Run

-- baas_bot_run 表
CREATE TABLE `baas_bot_run` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `run_id` varchar(64) NOT NULL COMMENT '运行ID (UUID)',
  `bot_id` varchar(128) NOT NULL COMMENT 'Bot ID (来自API Key的app_id)',
  `api_key_prefix` varchar(8) NOT NULL COMMENT 'API Key前缀 (用于审计)',
  `message` text DEFAULT NULL COMMENT '用户消息',
  `metadata` text DEFAULT NULL COMMENT '元数据(JSON)',
  `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT '状态: PENDING/RUNNING/COMPLETED/FAILED',
  `result_content` text DEFAULT NULL COMMENT 'Bot回复内容',
  `result_extra` text DEFAULT NULL COMMENT '结果额外信息(JSON)',
  `error` text DEFAULT NULL COMMENT '错误信息',
  `completed_at` timestamp NULL DEFAULT NULL COMMENT '完成时间',
  `message_long` mediumtext DEFAULT NULL COMMENT '用户消息(长文本)',
  `result_content_long` mediumtext DEFAULT NULL COMMENT 'Bot回复内容(长文本)',
  PRIMARY KEY(`id`),
  UNIQUE KEY `uk_run_id`(`run_id`)
) AUTO_INCREMENT = 1 DEFAULT CHARSET = utf8mb4 COMMENT = 'Bot Run 执行记录';

CREATE TABLE `baas_system_config` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `conf_key` varchar(256) NOT NULL COMMENT '配置key,以点为分隔符的1段或多段字符串key，以表达配置的层级结构',
  `conf_value` text DEFAULT NULL COMMENT '配置值',
  `env` varchar(32) NOT NULL COMMENT 'baas部署环境',
  `name` varchar(256) NOT NULL COMMENT '配置项显示名',
  `description` varchar(1024) DEFAULT NULL COMMENT '配置项说明',
  `creator` varchar(64) NOT NULL COMMENT '创建者',
  `modifier` varchar(64) NOT NULL COMMENT '最后修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_key` (`env`, `conf_key`) BLOCK_SIZE 16384 GLOBAL
) COMMENT = 'baas平台全局配置'

CREATE TABLE `baas_api_key` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `api_key_hash` varchar(128) NOT NULL COMMENT 'PBKDF2哈希',
  `api_key_prefix` varchar(8) NOT NULL COMMENT '密钥前缀',
  `key_name` varchar(128) DEFAULT NULL COMMENT '密钥名称',
  `app_id` varchar(128) NOT NULL COMMENT '应用ID',
  `app_type` varchar(64) DEFAULT NULL COMMENT '应用类型',
  `description` text DEFAULT NULL COMMENT '描述',
  `rate_limit_rpm` int(11) DEFAULT NULL COMMENT '每分钟请求数限制',
  `rate_limit_rpd` int(11) DEFAULT NULL COMMENT '每天请求数限制',
  `status` varchar(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: ACTIVE/INACTIVE/REVOKED',
  `owner` varchar(64) NOT NULL COMMENT 'Owner (创建时默认为creator)',
  `tenant` varchar(64) DEFAULT NULL COMMENT '租户标识',
  `env` varchar(32) NOT NULL COMMENT '环境类型',
  `creator` varchar(64) NOT NULL COMMENT '创建人',
  `modifier` varchar(64) DEFAULT NULL COMMENT '修改人',
  `policy` mediumtext DEFAULT NULL COMMENT '权限策略(JSON格式)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_api_key_prefix` (`api_key_prefix`) BLOCK_SIZE 16384 LOCAL,
  KEY `idx_key_prefix_status` (`api_key_prefix`, `status`) BLOCK_SIZE 16384 LOCAL,
  KEY `idx_app_id_status` (`app_id`, `status`) BLOCK_SIZE 16384 LOCAL
) COMMENT = 'API Keys'