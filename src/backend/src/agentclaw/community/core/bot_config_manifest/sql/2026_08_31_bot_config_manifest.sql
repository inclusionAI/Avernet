-- Bot config manifest document (issue #1469, W1).
--
-- One row per bot at most. The document is the source of truth the platform
-- converges a bot against at apply points (W4+); PUT replaces the whole
-- document, DELETE removes the declaration without touching any materialized
-- entity ("declaration absent" is "no opinion", not "empty set" — the latter
-- is `[]`, and that distinction lives in the document, not here).
--
-- Key is (avernet_tenant, manifest_key), where manifest_key is the same
-- length-prefixed sha256 surrogate ac_bot_startup_script uses over
-- (env, entity_id, bot_id). The tenant rides alongside rather than being
-- hashed in so the isolation boundary stays visible in the key; the surrogate
-- exists because entity_id alone (1024 utf8mb4 chars = 4096 bytes) passes
-- InnoDB's 3072-byte index-key cap before the other columns are counted.
--
-- The same stability reasoning as ac_bot_startup_script applies verbatim:
-- ac_bots carries UNIQUE KEY uk_bot_id_entity_id_env and bot deletion is a
-- soft update, so one (env, entity_id, bot_id) names at most one ac_bots row,
-- ever — a manifest row cannot be inherited by a later bot. Reads therefore
-- need no ownership check on top of the key; the guard above the service is
-- the public API's own-bot check.
--
-- `document` holds the service's canonical serialization of the parsed
-- document. JSON string values (the script body above all) round-trip
-- byte-exact through it, and explicitly-declared-but-empty categories stay
-- distinguishable from absent ones — the D2 distinction the apply layer
-- (W4) reads this storage for.
CREATE TABLE `ac_bot_config_manifest` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  -- 1024, matching ac_bots.entity_id exactly; not in the uniqueness key, so it
  -- is free to match its source (same reasoning as ac_bot_startup_script).
  `entity_id`     varchar(1024) NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  `schema_version` int(11)      NOT NULL COMMENT 'manifest schema 版本（v1=1）',
  `document`      mediumtext    NOT NULL COMMENT '配置清单文档 JSON 规范形态（整份替换，保空类目声明）',
  `size_bytes`    int(11)       NOT NULL COMMENT '文档字节数（UTF-8)',
  `modifier`      varchar(1024) NOT NULL COMMENT '审计：最后写入者',
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  -- Bounded surrogate for the length-prefixed (env, entity_id, bot_id); written
  -- by the repository, never by a caller. Uncomment of the encoding rationale
  -- lives in implementations/bot/config_manifest.py:_manifest_key.
  `manifest_key`  char(64)      NOT NULL COMMENT '唯一键代理：sha256(长度前缀 env/entity_id/bot_id)',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The only index; every read filters on manifest_key rather than the three
  -- columns it hashes (no second lookup key to drift out of step with the ORM).
  UNIQUE KEY `uk_tenant_manifest_key` (`avernet_tenant`, `manifest_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 配置清单文档';
