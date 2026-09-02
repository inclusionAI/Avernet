-- Platform-delivered files for a teclaw bot's manifest (issue #1476, W8 of the
-- bot-config-manifest plan in ``docs/bot-config-manifest/``).
--
-- The INDEX half of the platform's own copy of what a manifest delivered: one
-- row per file the platform put into the bot-data object store on a manifest's
-- behalf. The bytes are in the store under ``store_key``; this table says which
-- files exist per bot and category, with what digest, and which apply wrote
-- them. The teclaw composer reads it to put {store, path} refs into the
-- artifact; the store-backed materialisers converge it.
--
-- A row is a DELIVERED file, not a fetch event (that is ac_manifest_content).
-- Under the manifest's overwrite rule a category's rows are exactly its area;
-- a category the manifest does not declare has no rows and is left to the
-- engine (the artifact's ``ownership`` map says so).
--
-- KEY. (avernet_tenant, env, entity_id, bot_id, category, path_hash). The
-- readable ``rel_path`` is too wide for InnoDB's 3072-byte key cap (768 chars
-- is 3072 utf8mb4 bytes on its own), so uniqueness uses sha256(rel_path) —
-- the way ac_bot_startup_script hashed its key. The six columns come to
-- 64+20+256+256+32+64 = 692 chars = 2768 bytes. Every read is a prefix of it.
--
-- TENANT ISOLATION is load-bearing for the reason ac_bot_config_manifest gives:
-- a bot_id is unique only within a tenant.
CREATE TABLE `ac_bot_config_managed_files` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`            varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`      varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  `category`       varchar(32)   NOT NULL COMMENT '类目: identity/resources/skills',
  `name`           varchar(512)  NOT NULL COMMENT '条目名：identity 文件类型 / 资源路径 / skill 名',
  `rel_path`       varchar(768)  NOT NULL COMMENT '引擎相对路径，如 identity/RULES.md',
  `path_hash`      varchar(64)   NOT NULL COMMENT 'sha256(rel_path)，唯一键代理',
  `store_key`      varchar(1024) NOT NULL COMMENT 'bot-data store 对象键',
  `digest`         varchar(80)   NOT NULL COMMENT 'sha256:<hex>',
  `size_bytes`     int(11)       NOT NULL COMMENT '字节数',
  `apply_id`       varchar(64)   DEFAULT NULL COMMENT '写入它的 apply_id',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  `gmt_create`     datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`   datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_env_entity_bot_category_path` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `category`, `path_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 配置清单：平台托管文件索引（teclaw）';
