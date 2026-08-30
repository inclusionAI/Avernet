-- Gateway MySQL/OceanBase canonical schema for the Avernet identity tables.
--
-- Mirrors the bcs `migrations/mysql` DDL conventions: utf8mb4, bigint unsigned
-- auto-increment `id`, `gmt_create`/`gmt_modified` with CURRENT_TIMESTAMP (and
-- ON UPDATE CURRENT_TIMESTAMP), named `uk_`/`idx_` indexes, and per-column
-- `COMMENT` (following the backend SQL style).
--
-- This file is CREATE TABLE IF NOT EXISTS, so it only provisions a NEW database.
-- Changes to an already-deployed schema go in a numbered migration beside it
-- (see `002_application_api_key.sql` and
-- `003_application_app_name_env_unique.sql`); editing this file alone migrates
-- nobody. Every numbered migration is also folded back into the definitions
-- below, so a fresh database and a migrated one end up with the same schema.
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

-- The unique indexes on the two `token` columns span a 700-character prefix,
-- not the whole varchar(1024): at utf8mb4 the full width is a 4096-byte key,
-- past InnoDB's 3072-byte limit, which made this file non-executable as
-- previously written. Both token kinds are signed JWTs of ~261 characters, so a
-- 700-character prefix (2800 bytes) covers the entire value and uniqueness is
-- unaffected. `002_application_api_key.sql` makes the same change on deployed
-- databases.
--
-- `avernet_access_key_token.token` keeps the server-default (case-insensitive)
-- collation, so its exact-match lookup is case-insensitive on MySQL. Left alone
-- because access-key credentials are outside this change.

-- Table: avernet_application
-- 第三方应用注册表：按 `api_key_prefix`(API Key 前 8 位)定位、再比对 `api_key_hash`；
-- `id` 为应用稳定身份。每行只填一种凭证：新行填 api_key_*，迁移前的旧行填 `token`。
CREATE TABLE IF NOT EXISTS `avernet_application` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `app_name` varchar(256) NOT NULL COMMENT '应用名称',
  `app_type` varchar(64) NOT NULL DEFAULT 'UNKNOWN' COMMENT '应用类型',
  `api_key_hash` varchar(256) DEFAULT NULL COMMENT 'API Key 哈希(PBKDF2-SHA256，格式 base64(salt):base64(dk))',
  `api_key_prefix` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT 'API Key 前 8 位，查找键(哈希加盐，无法按哈希查找)',
  `token` varchar(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT '[废弃] 旧版应用令牌(明文签名 JWT)，过渡期精确匹配查找键；待废弃日志静默后随查找路径一并删除',
  `owners` varchar(1024) NOT NULL COMMENT '应用归属(开发者/组织)',
  `tenant` varchar(64) NOT NULL COMMENT '所属租户(逻辑引用 avernet_tenant.name)',
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态(ACTIVE/INACTIVE)，仅 ACTIVE 可通过鉴权',
  `env` varchar(64) NOT NULL DEFAULT '' COMMENT '环境标识',
  `config` json DEFAULT NULL COMMENT '扩展配置(JSON)',
  `creator` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `modifier` varchar(128) NOT NULL DEFAULT '' COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_avernet_application_api_key_prefix` (`api_key_prefix`),
  UNIQUE KEY `uk_avernet_application_token_prefix` (`token`(700)),
  -- 应用名在同一环境内唯一。`app_name` 是人从列表里认出自己应用的方式，
  -- 同一 `env` 下重名会让每一份列表都变得有歧义。
  --
  -- `env` 在键里而不是仅仅并排放着：一套库承载多个环境，同一个应用本就应当在
  -- 每个环境各有一行；只按 `app_name` 建键，在 dev 注册 "billing" 就会把这个
  -- 名字从 prod 里锁掉。
  --
  -- utf8mb4 下 1280 字节 (256x4 + 64x4)，远在 InnoDB 3072 字节上限之内。
  --
  -- 这个键取代了原先的 `idx_avernet_application_app_name`：`app_name` 是它的
  -- 前导列，B-tree 前缀扫描能服务原索引服务的每一次查找，同时保留两者等于
  -- 为同一条访问路径维护两棵树。`003_application_app_name_env_unique.sql`
  -- 在已部署的库上做同样的替换。
  UNIQUE KEY `uk_avernet_application_app_name_env` (`app_name`, `env`)
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
  UNIQUE KEY `uk_avernet_access_key_token_token` (`token`(700)),
  KEY `idx_avernet_access_key_token_access_key` (`access_key`)
) DEFAULT CHARSET = utf8mb4 COMMENT = '访问密钥注册表';
