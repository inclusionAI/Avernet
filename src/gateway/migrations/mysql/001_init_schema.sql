-- Gateway MySQL/OceanBase canonical schema for the Avernet identity tables.
--
-- Mirrors the bcs `migrations/mysql` DDL conventions: utf8mb4, bigint unsigned
-- auto-increment `id`, `gmt_create`/`gmt_modified` with CURRENT_TIMESTAMP (and
-- ON UPDATE CURRENT_TIMESTAMP), named `uk_`/`idx_` indexes, and per-column
-- `COMMENT` (following the backend SQL style).
--
-- The gateway's bare/community edition creates these tables from ORM metadata
-- via `DataSourcePlugin.create_all()` (in-memory SQLite; the bare plugin
-- downgrades BIGINT PKs to INTEGER automatically). This DDL is the canonical
-- schema for the real MySQL/OceanBase deployment.
--
-- NOTE: `bcs_bots` — the bot registry the gateway reads — is owned by the bcs
-- schema (src/bcs/migrations/mysql/001_init_schema.sql) and is intentionally
-- NOT redefined here. The gateway owns only the `avernet_*` identity tables
-- below: the third-party-app registry (`avernet_application`), the tenant master
-- (`avernet_tenant`), and the access-key registry (`avernet_access_key_token`).

-- Table: avernet_application
-- 第三方应用注册表：按 opaque `token`(签名 JWT) 查找；`id` 为应用稳定身份。
CREATE TABLE IF NOT EXISTS `avernet_application` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `app_name` varchar(256) NOT NULL COMMENT '应用名称',
  `app_type` varchar(64) NOT NULL DEFAULT 'UNKNOWN' COMMENT '应用类型',
  `token` varchar(1024) NOT NULL COMMENT '应用访问令牌(签名 JWT)，opaque 查找键',
  `owners` varchar(1024) NOT NULL COMMENT '应用归属(开发者/组织)',
  `tenant` varchar(64) NOT NULL COMMENT '所属租户(逻辑引用 avernet_tenant.name)',
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态(ACTIVE/INACTIVE)',
  `env` varchar(64) NOT NULL DEFAULT '' COMMENT '环境标识',
  `config` json DEFAULT NULL COMMENT '扩展配置(JSON)',
  `creator` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `modifier` varchar(128) NOT NULL DEFAULT '' COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_avernet_application_token` (`token`),
  KEY `idx_avernet_application_app_name` (`app_name`)
) DEFAULT CHARSET = utf8mb4 COMMENT = '第三方应用注册表';

-- Table: avernet_tenant
-- 租户主数据表：`name` 为租户 code，被 application / access_key 的 `tenant` 逻辑引用(无 DB FK)。
CREATE TABLE IF NOT EXISTS `avernet_tenant` (
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
) DEFAULT CHARSET = utf8mb4 COMMENT = '租户主数据表';

-- Table: avernet_access_key_token
-- 访问密钥注册表：按 opaque `token`(签名 JWT) 查找；`expire_at` 为过期时间。
-- 审计列不上浮到 RegisteredAccessKey SPI(对照 bcs_bots 的 env)。
CREATE TABLE IF NOT EXISTS `avernet_access_key_token` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `token` varchar(1024) NOT NULL COMMENT '访问密钥令牌(签名 JWT)，opaque 查找键',
  `access_key` varchar(256) NOT NULL COMMENT '访问密钥ID',
  `tenant` varchar(64) NOT NULL COMMENT '所属租户(逻辑引用 avernet_tenant.name)',
  `expire_at` timestamp NOT NULL COMMENT '过期时间',
  `creator` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `modifier` varchar(128) NOT NULL DEFAULT '' COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_avernet_access_key_token_token` (`token`),
  KEY `idx_avernet_access_key_token_access_key` (`access_key`)
) DEFAULT CHARSET = utf8mb4 COMMENT = '访问密钥注册表';
