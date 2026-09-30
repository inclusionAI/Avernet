CREATE TABLE `avernet_access_key_token` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `token` varchar(1024) NOT NULL COMMENT '访问密钥令牌(签名 JWT)，opaque 查找键',
  `access_key` varchar(256) NOT NULL COMMENT '访问密钥ID',
  `tenant` varchar(64) NOT NULL COMMENT '所属租户(逻辑引用 avernet_tenant.name)',
  `expire_at` timestamp NOT NULL DEFAULT '2037-12-31 23:59:59' COMMENT '过期时间',
  `creator` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `modifier` varchar(128) NOT NULL DEFAULT '' COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_avernet_access_key_token_token` (`token`),
  KEY `idx_avernet_access_key_token_access_key` (`access_key`)
) COMMENT = '访问密钥注册表';

CREATE TABLE `avernet_application` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `app_name` varchar(256) NOT NULL COMMENT '应用名称',
  `app_type` varchar(64) NOT NULL DEFAULT 'UNKNOWN' COMMENT '应用类型',
  `token` varchar(1024) DEFAULT NULL COMMENT '[废弃] 旧版应用令牌(明文签名 JWT)，过渡期精确匹配查找键；待废弃日志静默后随查找路径一并删除',
  `owners` varchar(1024) NOT NULL COMMENT '应用归属(开发者/组织)',
  `tenant` varchar(64) NOT NULL COMMENT '所属租户(逻辑引用 avernet_tenant.name)',
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态(ACTIVE/INACTIVE)，仅 ACTIVE 可通过鉴权',
  `env` varchar(64) NOT NULL DEFAULT '' COMMENT '环境标识',
  `config` json DEFAULT NULL COMMENT '扩展配置(JSON)',
  `creator` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `modifier` varchar(128) NOT NULL DEFAULT '' COMMENT '修改人',
  `api_key_hash` varchar(256) DEFAULT NULL COMMENT 'API Key 哈希(PBKDF2-SHA256，格式 base64(salt):base64(dk))',
  `api_key_prefix` varchar(32) DEFAULT NULL COMMENT 'API Key 前 8 位，查找键(哈希加盐，无法按哈希查找)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_avernet_application_token` (`token`),
  UNIQUE KEY `uk_avernet_application_api_key_prefix` (`api_key_prefix`),
  KEY `idx_avernet_application_app_name` (`app_name`)
) COMMENT = '第三方应用注册表';

CREATE TABLE `avernet_tenant` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `name` varchar(128) NOT NULL COMMENT '租户名称(唯一)',
  `description` varchar(1024) NOT NULL DEFAULT '' COMMENT '租户描述',
  `owner` varchar(128) NOT NULL DEFAULT '' COMMENT '租户归属',
  `config` json DEFAULT NULL COMMENT '扩展配置(JSON)',
  `creator` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `modifier` varchar(128) NOT NULL DEFAULT '' COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_avernet_tenant_name` (`name`)
) COMMENT = '租户主数据表';
