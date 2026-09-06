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
-- Key is (avernet_tenant, env, entity_id, bot_id) — the logical key itself, no
-- surrogate. Two things make that work and matter:
--
--   * INDEX BUDGET. InnoDB caps an index key at 3072 bytes and utf8mb4 counts 4
--     bytes per character, so the widths here are part of the constraint rather
--     than free choices. entity_id is varchar(256) — NOT ac_bots' 1024, which
--     would be 4096 bytes on its own and would have CREATE TABLE refused. The
--     four columns come to 2384 bytes (64+20+256+256 chars), leaving headroom.
--     ac_bot_startup_script hashes the same logical key into a surrogate
--     instead; it did not have to, and this table does not repeat it.
--   * TENANT ISOLATION. ac_bots is itself tenant-scoped, so a bot_id is unique
--     only *within* a tenant, and legacy "default" bots carry documented
--     residual cross-tenant collision on that identifier. Without the tenant
--     here two such bots share one manifest row, and either tenant could
--     overwrite the other's manifest — which, once apply lands (W4), decides
--     what is installed into the other tenant's container.
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
-- DIALECT: OCEANBASE, MYSQL MODE. What this file writes follows the deployed
-- tables there rather than portable MySQL -- compare ``SHOW CREATE TABLE
-- ac_bots``, and the sibling DDL in core/caller_identity/sql/.
--
--   * EVERY INDEX IS DECLARED ``GLOBAL``. On a partitioned table an index
--     without it is partition-local, and a partition-local UNIQUE enforces
--     uniqueness only *within* a partition. These tables are not
--     partitioned today, so it is a no-op now and cheap insurance later: what
--     it prevents surfaces as duplicate rows, silently, never as an error.
--     SQLAlchemy cannot render the modifier, so this file is the only place it
--     can live.
--   * ``TIMESTAMP``, NOT ``DATETIME``, for gmt_* and the business timestamps,
--     matching ac_bots and every table added since. Mixing the two is what
--     skill_center/sql/2026_09_03_align_space_skill_timestamps_with_gmt.sql
--     had to repair: TIMESTAMP converts by session time zone and DATETIME does
--     not, so a session outside Asia/Shanghai reads the two as disagreeing by
--     the offset. The ORM keeps ``DateTime`` either way -- ac_skill_version's
--     published_at is the precedent for that pairing.
--   * NO ``ENGINE`` CLAUSE, and no BLOCK_SIZE / REPLICA_NUM / COMPRESSION /
--     TABLET_SIZE / PCTFREE / ROW_FORMAT. OceanBase applies its own defaults
--     and echoes them back from SHOW CREATE TABLE; writing them here would be
--     copying its own output back at it.

CREATE TABLE `ac_bot_config_manifest` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`            varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  -- 256, not ac_bots' 1024: this column IS in the uniqueness key, so its width
  -- is spent against the 3072-byte index budget. Real entity_ids are short user
  -- ids (u_165137) and 256 is what the newer tables here give one, while still
  -- leaving the key well inside the cap.
  `entity_id`      varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
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
  `gmt_create`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`   timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The only index, and every read uses it: the repository filters on env,
  -- entity_id and bot_id, which are exactly this key's columns after the tenant
  -- the guard supplies. No second lookup key to drift out of step with it.
  UNIQUE KEY `uk_tenant_env_entity_bot`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`) GLOBAL
) DEFAULT CHARSET = utf8mb4 COMMENT = 'Bot 配置清单';
