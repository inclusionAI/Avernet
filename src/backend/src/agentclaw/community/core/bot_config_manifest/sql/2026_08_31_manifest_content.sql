-- Manifest content store: platform-side copies of fetched sources (issue
-- #1510, W11 of the bot-config-manifest plan; the requirement is §2.8 of
-- ``docs/bot-config-manifest/work-items.zh-CN.md`` — hard requirement, audit
-- and reconciliation).
--
-- This table is the PROVENANCE LOG. The bytes themselves never live here:
-- schema §5 caps one entry at 100–200 MiB, and a column holding that is a
-- self-destructing design (max_allowed_packet, InnoDB row budget, every
-- backup suddenly a content store). Bytes live in the content-addressed blob
-- directory (``user_config.bot_config_manifest.content_store_dir``); this
-- table answers "WHERE did these bytes come from, for WHICH bot, WHEN".
--
-- NO UNIQUE KEY, ON PURPOSE — the log is append-only. The same digest fetched
-- again (a new apply, a retry, another bot) is a NEW row: that repetition is
-- exactly the audit fact "when, from where, on whose behalf". Deduplication
-- happens in the blob layer by content address; provenance is per-event.
-- Nothing in this table is ever updated, only inserted.
--
-- The bot axes mirror ac_bot_config_manifest's key (avernet_tenant, env,
-- entity_id, bot_id): a bot_id is unique only within a tenant (ac_bots is
-- itself tenant-scoped; legacy `default` bots collide on the identifier), so
-- a row without the tenant could answer "what did THIS tenant's bot receive"
-- with another tenant's receipts. entity_id is a storage key resolved from
-- the bot record server-side and never a public field (same rule as W1).
--
-- digest IS the address of the content: ``sha256:<64 hex>``, the fetcher's
-- own vocabulary (W2). Blob path is derived from it — no path column to
-- drift. It is NOT unique here (see above).
--
-- BOTH URLs are stored WITH path but WITHOUT userinfo and WITHOUT query.
-- Userinfo is refused by the guarded fetcher before any wire contact anyway;
-- a query string is where signed-source tokens live — the same reason W2
-- logs host-only. The reconciliation anchor for audit is the digest, never a
-- one-time signed URL. source_url is the manifest entry's source after
-- ${BOT_*} substitution; fetched_url is the final hop after redirects — when
-- the two differ, a redirect happened, and "where it came from" wants both
-- facts. git sources (W7) will carry their resolved ref/SHA in source_ref.
--
-- credential_name is the NAME ONLY — a W3 ac_source_credential identifier,
-- whose value is AES-GCM ciphertext in its own table, rotated by plain
-- re-PUT, and never re-readable in the clear. Storing anything beyond the
-- name here would copy a tenant token into an append-only audit log that by
-- this table's own policy is never deleted — the one place a leak could not
-- even be cleaned up. Test-pinned.
--
-- RETENTION POLICY (stated against §2.8, deliberately explicit): v1 retains
-- rows and blobs unconditionally — no delete, no sweep, no TTL. The audit
-- requirement is "answer what THIS bot received, from where, at that time";
-- until an audit horizon is named, any deletion is a manufactured audit gap,
-- and a cleanup that misjudges it is unrecoverable by design. The blob layer
-- is hence self-consistent: a digest's bytes exist for as long as any row
-- references them, and rows never go away. A retention window, when audit
-- names one, lands here as a comment change plus a sweep mechanism — not as
-- a silent default.
CREATE TABLE `ac_manifest_content` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`     varchar(256)  NOT NULL COMMENT '实体ID（存储键，服务端解析，非公开字段）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  -- 71 utf8mb4 chars = 284 bytes; indexed, and 284 is well inside the
  -- 3072-byte InnoDB key budget. No varchar padding games: the digest
  -- vocabulary ('sha256:' + hex) is fixed-width for its whole life.
  `digest`        char(71)      NOT NULL COMMENT '内容地址 sha256:<hex64>；blob 路径由它派生',
  -- Not indexed; 2048 chars is the doc-wide URL ceiling. utf8mb4 only
  -- spends the budget per actual bytes.
  `source_url`    varchar(2048) NOT NULL COMMENT '条目源 URL（${BOT_*} 替换后；去 userinfo/query）',
  `fetched_url`   varchar(2048) NOT NULL COMMENT '最终跳达 URL（重定向后；去 userinfo/query）',
  `credential_name` varchar(128) DEFAULT NULL COMMENT '凭证名（仅名字，W3 表内才有密文；无凭证为 NULL）',
  -- nullable: an inline content_type header is optional on the wire
  `content_type`  varchar(256)  DEFAULT NULL COMMENT '响应 Content-Type',
  `size_bytes`    bigint(20) unsigned NOT NULL COMMENT '字节数（与 digest 同为对账锚）',
  `fetched_at`    datetime      NOT NULL COMMENT '拉取时间（FetchedObject.fetched_at）',
  `modifier`      varchar(1024) NOT NULL DEFAULT '' COMMENT '审计：触发拉取的身份',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The audit query shape: one bot's receipts, newest first. Leading column
  -- tenant keeps it aligned with the guard's filter.
  KEY `idx_tenant_env_entity_bot` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `gmt_create`),
  -- keep_last / dedupe introspection: "which fetches ever produced this
  -- content" is the question blob hit-misses cannot answer alone.
  KEY `idx_digest` (`digest`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='manifest 拉取内容平台副本的溯源日志（行 append-only，字节在内容寻址 blob 目录）';
