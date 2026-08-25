CREATE TABLE IF NOT EXISTS `baas_api_key` (
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
  UNIQUE KEY `uk_api_key_prefix` (`api_key_prefix`) ,
  KEY `idx_key_prefix_status` (`api_key_prefix`, `status`) ,
  KEY `idx_app_id_status` (`app_id`, `status`)
) COMMENT = 'API Keys';

CREATE TABLE IF NOT EXISTS `baas_bot` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `bot_uuid` varchar(128) NOT NULL COMMENT '多版本追踪 UUID',
  `tenant` varchar(64) NOT NULL COMMENT '租户 ID',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING | ACTIVE | FAILED | RELEASED',
  `name` varchar(256) NOT NULL COMMENT 'Bot 名称',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `template_uuid` varchar(128) DEFAULT NULL COMMENT 'FK to baas_device_template.template_uuid (不可变引用)',
  `replica_desired` int(11) NOT NULL DEFAULT '1' COMMENT '目标实例数',
  `replica_minimum` int(11) NOT NULL DEFAULT '1' COMMENT '最小实例数',
  `replica_maximum` int(11) NOT NULL DEFAULT '10' COMMENT '最大实例数',
  `auto_scaling_enabled` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否启用自动伸缩',
  `sla_grade` varchar(32) NOT NULL DEFAULT 'standard' COMMENT 'SLA 等级: standard | premium | enterprise',
  `extra_config` mediumtext DEFAULT NULL COMMENT '扩展配置',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tnt_bot_uuid_env_status_del` (`tenant`, `env`, `bot_uuid`, `status`, `is_deleted`) ,
  KEY `idx_bot_uuid` (`bot_uuid`) ,
  KEY `idx_tenant_status` (`tenant`, `status`)
) COMMENT = 'Bot 表';

CREATE TABLE IF NOT EXISTS `baas_bot_device_rel` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户 ID',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id',
  `device_uuid` varchar(128) NOT NULL COMMENT 'FK to baas_device.device_uuid (不可变引用)',
  PRIMARY KEY (`id`),
  KEY `idx_bot_id` (`bot_id`) ,
  KEY `idx_device_uuid` (`device_uuid`)
) COMMENT = 'Bot 设备关系表';

CREATE TABLE IF NOT EXISTS `baas_bot_qpm_config` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `bot_id` varchar(128) NOT NULL COMMENT 'Bot ID',
  `qpm` int(11) NOT NULL DEFAULT '60' COMMENT '每分钟请求数上限',
  `env` varchar(32) DEFAULT NULL COMMENT '环境（dev/pre/prod）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_id_env` (`bot_id`, `env`)
) COMMENT = 'Bot QPM 限流配置';

CREATE TABLE IF NOT EXISTS `baas_bot_run` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `run_id` varchar(128) NOT NULL COMMENT '运行ID (UUID)',
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
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_id` (`run_id`)
) COMMENT = 'Bot Run 执行记录';

CREATE TABLE IF NOT EXISTS `baas_bot_run_queue` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `run_id` varchar(128) NOT NULL COMMENT '与 baas_bot_run.run_id 关联（1:1）',
  `bot_id` varchar(128) NOT NULL COMMENT 'Bot ID（<real_bot_id>:<entity_id>）',
  `session_id` varchar(128) DEFAULT NULL COMMENT '会话ID（session 串行锁 key）',
  `status` varchar(32) NOT NULL COMMENT '工作项状态：PENDING/RUNNING/DONE',
  `assigned_worker` varchar(64) DEFAULT NULL COMMENT '认领该工作项的 Worker 标识',
  `last_heartbeat` timestamp NULL DEFAULT NULL COMMENT '请求级心跳时间戳（宕机恢复判活）',
  `meta` text DEFAULT NULL COMMENT '工作项元数据 JSON（含 callback_function、bcn_callback_sent 等）',
  `env` varchar(64) DEFAULT 'pre' COMMENT '执行环境',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_id` (`run_id`) ,
  KEY `idx_status_bot_created` (`status`, `bot_id`, `gmt_create`) ,
  KEY `idx_status_heartbeat` (`status`, `last_heartbeat`) ,
  KEY `idx_status_env_bot_created` (`status`, `env`, `bot_id`, `gmt_create`)
) COMMENT = 'BotRun 队列工作项';

CREATE TABLE IF NOT EXISTS `baas_bot_run_queue_chunk` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `run_id` varchar(128) NOT NULL COMMENT '关联 baas_bot_run.run_id',
  `seq` int(11) NOT NULL COMMENT 'chunk 序号，严格递增',
  `chunk_type` varchar(16) NOT NULL COMMENT 'delta / final / error / usage / agent',
  `content` mediumtext DEFAULT NULL COMMENT 'chunk 内容 (JSON 或纯文本)',
  `metadata` text DEFAULT NULL COMMENT 'chunk 元数据 JSON (engine_frame 等)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_seq` (`run_id`, `seq`) ,
  KEY `idx_run_seq` (`run_id`, `seq`)
) COMMENT = 'Bot 队列流式 chunk';

CREATE TABLE IF NOT EXISTS `baas_bot_session` (
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
  UNIQUE KEY `uk_session_id` (`session_id`) ,
  KEY `idx_time_bot` (`gmt_create`, `bot_uuid`) ,
  KEY `idx_bot_ik_time_dev` (`bot_uuid`, `invoker`, `gmt_create`, `device_uuid`)
) COMMENT = 'bot请求会话表，记录针对bot的业务调用';

CREATE TABLE IF NOT EXISTS `baas_device` (
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
  UNIQUE KEY `uk_debice_uuid` (`device_uuid`) ,
  KEY `idx_provider_device_id` (`provider_device_id`) ,
  KEY `idx_provider_device_id_env_is_deleted` (`env`, `is_deleted`, `provider_device_id`, `id`)
) COMMENT = '设备表';

CREATE TABLE IF NOT EXISTS `baas_device_template` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `template_uuid` varchar(128) NOT NULL COMMENT '多版本追踪 UUID',
  `tenant` varchar(64) NOT NULL COMMENT '租户name',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `status` varchar(32) NOT NULL DEFAULT 'CREATED' COMMENT 'CREATED | AUDITED | ONLINE | OFFLINE',
  `name` varchar(64) NOT NULL COMMENT '模板名称: openclaw, moltis, etc.',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `config` mediumtext DEFAULT NULL COMMENT '设备配置 (endpoint, model, capabilities, etc.)',
  `template_id` bigint(20) NOT NULL COMMENT '资源模板的唯一编号，会被用作paas设备id的编码中,全局唯一',
  `type` varchar(32) NOT NULL COMMENT '资源Paas类型，例如ARCA或SIGMA等',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_template_id` (`template_id`) ,
  KEY `idx_template_uuid` (`template_uuid`)
) COMMENT = '设备模板表';

CREATE TABLE IF NOT EXISTS `baas_file_transfer_tickets` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `transfer_id` varchar(128) NOT NULL COMMENT 'UUID, 唯一标识一次传输',
  `paas_device_id` varchar(512) NOT NULL COMMENT '{platform_device_id}@{template_id},  上传或下载文件的具体发起设备的标识',
  `tenant` varchar(128) NOT NULL COMMENT '租户标识',
  `device_path` varchar(1024) DEFAULT NULL COMMENT 'device 上的目标/源路径',
  `fileservice_staging_path` varchar(1024) NOT NULL COMMENT '文件服务暂存路径: file-transfers/{transfer_id}/{filename}',
  `status` varchar(32) NOT NULL COMMENT 'CREATED|UPLOADING|UPLOAD_COMPLETED|PULLING|PUSHING|DONE|FAILED',
  `direction` varchar(16) NOT NULL COMMENT 'UPLOAD | DOWNLOAD',
  `staging_subdir` varchar(1024) DEFAULT NULL COMMENT '用户指定逻辑子目录，支持 / 多级',
  `filename` varchar(512) NOT NULL COMMENT '文件名',
  `error_message` text DEFAULT NULL COMMENT '错误信息, if any',
  `env` varchar(16) NOT NULL COMMENT '部署环境',
  `operator` varchar(256) NOT NULL COMMENT '操作方，可能是人或系统，最好能精确到人',
  `download_url` varchar(2048) DEFAULT NULL COMMENT '下载预签名 URL',
  `multipart_session_id` varchar(256) DEFAULT NULL COMMENT '分片上传 upload_id',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_transferid` (`env`, `transfer_id`) ,
  KEY `idx_status_gmt` (`status`, `gmt_create`) ,
  KEY `idx_env_operator` (`env`, `operator`)
) COMMENT = 'baas bot设备文件上传下载记录表';

CREATE TABLE IF NOT EXISTS `baas_local_user_machine` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `template_id` bigint(20) NOT NULL COMMENT 'baas_device_template的template_id唯一性字段',
  `user_id` varchar(128) NOT NULL COMMENT '用户标识',
  `machine_id` varchar(128) NOT NULL COMMENT '本地设备平台标识，如用户的某台mac电脑',
  `machine_info` text DEFAULT NULL COMMENT '本地设备平台其它信息',
  `last_heartbeat` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地设备平台最后一次心跳时间',
  `connected_server_instance` varchar(512) NOT NULL COMMENT '本地设备平台当前保持连接的后端管理拼台的节点标识',
  `status` varchar(64) NOT NULL COMMENT '本地设备平台状态：ONLINE / OFFLINE / DISABLED',
  `env` varchar(16) NOT NULL COMMENT '部署环境',
  `connected_route_info` varchar(1024) DEFAULT NULL COMMENT '调用路由信息',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_machine_env` (`machine_id`, `env`) ,
  KEY `idx_user_env` (`user_id`, `env`)
) COMMENT = '用户、本地bot平台设备节点关系表';

CREATE TABLE IF NOT EXISTS `baas_local_ws_relay_session` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `session_id` varchar(128) NOT NULL COMMENT 'relay 会话唯一标识',
  `machine_id` varchar(128) NOT NULL COMMENT '工作机唯一标识',
  `connected_server_instance` varchar(512) NOT NULL COMMENT 'mng 连接所在 agentclawproxy 实例 IP',
  `status` varchar(64) NOT NULL COMMENT 'active/closed',
  `env` varchar(16) NOT NULL COMMENT '部署环境',
  `gmt_close` timestamp NULL DEFAULT NULL COMMENT '关闭时间',
  `connected_route_info` varchar(1024) NOT NULL COMMENT '单节点内调用路由信息',
  `operator` varchar(128) NOT NULL COMMENT '对话发起方标识，例如工号',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_session` (`session_id`, `env`)
) COMMENT = '桌面bot聊天会话表';

CREATE TABLE IF NOT EXISTS `baas_publish` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户 ID',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id',
  `publish_type` varchar(32) NOT NULL DEFAULT 'CREATE' COMMENT 'CREATE | SCALE_UP | SCALE_DOWN | RESTART | UPDATE',
  `name` varchar(256) NOT NULL COMMENT '发布名称',
  `description` varchar(1024) DEFAULT NULL COMMENT '描述',
  `publisher` varchar(32) NOT NULL COMMENT '发布人 (工号:花名)',
  `replica_desired` int(11) NOT NULL DEFAULT '1' COMMENT '目标设备数',
  `batch_capacity` int(11) NOT NULL DEFAULT '1' COMMENT '每批设备数',
  `batch_number` int(11) DEFAULT NULL COMMENT '滚动重启批次数量',
  `cooldown_seconds` int(11) NOT NULL DEFAULT '30' COMMENT '冷却等待秒数',
  `config_version` varchar(64) DEFAULT NULL COMMENT '配置版本号',
  `status` varchar(32) NOT NULL DEFAULT 'INIT' COMMENT 'INIT | PENDING | ACTIVE | FAILED | RELEASE | APPROVING | REJECTED | REVOKED',
  `last_publish_id` bigint(20) unsigned DEFAULT NULL COMMENT '上次成功发布 ID',
  `changelog` varchar(4096) DEFAULT NULL COMMENT '变更说明',
  `extra_config` mediumtext DEFAULT NULL COMMENT '扩展配置',
  PRIMARY KEY (`id`),
  KEY `idx_bot_id` (`bot_id`)
) COMMENT = 'Bot 发布表';

CREATE TABLE IF NOT EXISTS `baas_publish_batch` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户 ID',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
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
  `extra_config` mediumtext DEFAULT NULL COMMENT '扩展配置',
  PRIMARY KEY (`id`),
  KEY `idx_publish_id` (`publish_id`)
) COMMENT = 'Bot 发布批次表';

CREATE TABLE IF NOT EXISTS `baas_publish_record` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(64) NOT NULL COMMENT '租户 ID',
  `env` varchar(16) NOT NULL COMMENT '环境',
  `domain` varchar(128) NOT NULL COMMENT '域',
  `is_deleted` bigint(20) NOT NULL DEFAULT '0' COMMENT '软删除标记 (0=正常, >0=归档记录ID)',
  `creator` varchar(128) NOT NULL COMMENT '创建人 ID',
  `modifier` varchar(128) NOT NULL COMMENT '修改人 ID',
  `device_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_device.id',
  `bot_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_bot.id (反范式化)',
  `publish_id` bigint(20) unsigned DEFAULT NULL COMMENT 'FK to baas_publish.id',
  `batch_id` bigint(20) unsigned NOT NULL COMMENT 'FK to baas_publish_batch.id',
  `event_type` varchar(32) NOT NULL COMMENT 'SCALE_UP | SCALE_DOWN | START | STOP | RESTART',
  `trigger_source` varchar(32) DEFAULT 'manual' COMMENT 'manual | auto | api | system',
  `publish_reason` varchar(1000) DEFAULT NULL COMMENT '原因',
  `result_status` varchar(32) DEFAULT 'SUCCESS' COMMENT 'SUCCESS | FAILED | PARTIAL',
  `result_message` varchar(4096) DEFAULT NULL COMMENT '结果信息',
  `extra_config` mediumtext DEFAULT NULL COMMENT '扩展配置',
  PRIMARY KEY (`id`),
  KEY `idx_batch_id` (`batch_id`) ,
  KEY `idx_device_id` (`device_id`)
) COMMENT = 'Bot 发布记录表';

CREATE TABLE IF NOT EXISTS `baas_resource_key` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `tenant` varchar(128) NOT NULL COMMENT '租户',
  `resource_key` varchar(128) NOT NULL COMMENT 'bot资源标识',
  `app` varchar(128) NOT NULL COMMENT '应用名称',
  PRIMARY KEY (`id`)
) COMMENT = 'bot资源key,支持gateway';

CREATE TABLE IF NOT EXISTS `baas_resource_key_bot_mapping` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `resource_key_id` bigint(20) unsigned NOT NULL COMMENT 'baas_resource_key.id',
  `bot_id` varchar(128) NOT NULL COMMENT 'bot唯一标识',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_resource_key_id_bot_id` (`resource_key_id`, `bot_id`) ,
  KEY `idx_bot_id` (`bot_id`)
) COMMENT = 'resource_key与bot多对多映射表,支持gateway';

CREATE TABLE IF NOT EXISTS `baas_session_file_tickets` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `transfer_id` varchar(128) NOT NULL COMMENT '传输唯一标识',
  `tenant` varchar(128) NOT NULL COMMENT '租户',
  `session_id` varchar(256) NOT NULL COMMENT '文件属于的会话标识',
  `status` varchar(32) NOT NULL COMMENT '文件状态',
  `staging_subdir` varchar(512) DEFAULT NULL COMMENT '虚拟子目录,可用于自行构造目录层级概念',
  `filename` varchar(512) NOT NULL COMMENT '文件名',
  `fileservice_staging_path` varchar(1024) NOT NULL COMMENT ' FileService存储路径，内部实现概念，不对外暴露',
  `error_message` text DEFAULT NULL COMMENT '错误信息',
  `multipart_session_id` varchar(256) DEFAULT NULL COMMENT '分片上传会话 ID',
  `env` varchar(16) NOT NULL COMMENT '环境标识',
  `operator` varchar(256) NOT NULL DEFAULT 'unknown' COMMENT '操作者',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tid` (`transfer_id`) ,
  KEY `idx_env_tnt_sid` (`env`, `tenant`, `session_id`) ,
  KEY `idx_env_tnt_dir` (`env`, `tenant`, `staging_subdir`)
) COMMENT = '会话级文件分享记录表';

CREATE TABLE IF NOT EXISTS `baas_system_config` (
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
  UNIQUE KEY `uk_env_key` (`env`, `conf_key`)
) COMMENT = 'baas平台全局配置';

CREATE TABLE IF NOT EXISTS `baas_tenant` (
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
) COMMENT = '租户表';
