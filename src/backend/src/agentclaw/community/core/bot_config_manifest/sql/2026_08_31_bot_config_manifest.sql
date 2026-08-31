-- Per-bot configuration manifest (issue #1469, W1 of the bot-config-manifest
-- plan in ``docs/bot-config-manifest/``).
--
-- One row per bot at most. The stored value is the manifest **document as the
-- caller wrote it** — the exact bytes, not a re-serialisation of a parsed tree.
-- Clearing the manifest deletes the row rather than storing an empty document,
-- so "no row" and "no manifest" are the same state and a bot that never had one
-- reads as an empty document rather than an error.
--
-- WHY THE DOCUMENT IS STORED VERBATIM. ``script.body`` inside it is a shell
-- body, and a shell body means nothing unless it survives byte for byte: a
-- quote, a ``$(id)``, a ``{token}``, a trailing newline and the exact indent of
-- a YAML block scalar are all load-bearing. Round-tripping through a YAML
-- emitter preserves the *value* and not the bytes, and the difference is a
-- caller's script silently changing between the PUT that was accepted and the
-- container that runs it. The parse is for validation only; what is stored and
-- what is read back is the submission.
--
-- Key is (avernet_tenant, manifest_key), where manifest_key is a sha256 of the
-- logical key (env, entity_id, bot_id) — the same shape, for the same two
-- reasons, as ``ac_bot_startup_script``:
--
--   * INDEX BUDGET. entity_id is 1024 utf8mb4 characters = 4096 bytes, past
--     InnoDB's 3072-byte index-key cap on its own, so the constraint is carried
--     on a fixed-width digest instead of on the three columns directly. The
--     columns keep the widths of their sources rather than being narrowed to
--     fit an index.
--   * TENANT ISOLATION. ac_bots is itself tenant-scoped, so a bot_id is unique
--     only *within* a tenant, and legacy "default" bots carry documented
--     residual cross-tenant collision on that identifier. Without the tenant
--     here two such bots share one manifest row, and either tenant could
--     overwrite the other's manifest — which, once apply lands (W4), decides
--     what is installed into the other tenant's container. The tenant is
--     carried alongside the digest rather than hashed into it so the isolation
--     boundary stays visible in the key itself.
--
-- WHY EVERY READ IS A PLAIN LOOKUP WITH NO OWNERSHIP CHECK ON TOP: ac_bots
-- carries UNIQUE KEY uk_bot_id_entity_id_env (bot_id, entity_id, env) with
-- is_delete NOT part of it, and bot deletion is a soft update, so a deleted bot
-- goes on occupying that tuple. One (env, entity_id, bot_id) therefore names at
-- most one ac_bots row, ever, and a manifest row cannot be inherited by a later
-- bot because there is no later bot. If that unique key is ever dropped or
-- narrowed, this table needs an owner stamp again.
--
-- entity_id is a storage key only: it is resolved server-side from the bot
-- record and is never a request parameter or a response field on the public API.
CREATE TABLE `ac_bot_config_manifest` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`            varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  -- 1024, matching ac_bots.entity_id exactly. It is NOT in the uniqueness key
  -- (see manifest_key below), so it is free to match its source.
  `entity_id`      varchar(1024) NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  -- mediumtext, not text: the document cap is 64 KiB (schema §5), which fits
  -- either, but a manifest is caller-authored content whose limit is a product
  -- decision and text's 65,535 bytes would put the column one raised limit away
  -- from truncating a document the API had already accepted.
  `document`       mediumtext    NOT NULL COMMENT '配置清单文档原文（清空即删行）',
  `size_bytes`     int(11)       NOT NULL COMMENT '文档字节数（UTF-8）',
  -- Denormalised from the document so an operator can count documents by
  -- version without parsing every row — which is the question asked when a
  -- schema version is retired. The parser is what decides the value; a row can
  -- only exist if validation accepted the version.
  `schema_version` int(11)       NOT NULL COMMENT 'schema 版本（当前仅 1）',
  `modifier`       varchar(1024) NOT NULL COMMENT '审计：最后写入者',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  `manifest_key`   char(64)      NOT NULL COMMENT '唯一键代理：sha256(env|entity_id|bot_id)',
  `gmt_create`     datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`   datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The only index, and every read uses it: the repository filters on
  -- manifest_key rather than on the three columns it hashes, so there is no
  -- second lookup key here to drift out of step with the ORM model.
  UNIQUE KEY `uk_tenant_manifest_key` (`avernet_tenant`, `manifest_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 配置清单';
