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
-- table answers "WHERE did the platform fetch bytes from, on WHICH bot's
-- behalf, WHEN". A row is a fetch/store EVENT, not a delivery: under §3.2's
-- all-or-nothing category overwrite an entry can be fetched, verified and
-- filed here and never materialised, because a sibling entry in its category
-- failed — read alone, this table OVER-REPORTS what a bot "received". What
-- an apply delivered is the apply record's question; the apply_id columns
-- below link the two.
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
-- facts. IPv6 literals keep their brackets: httpx's ``URL.host`` returns
-- them bare, and a bare address makes the port ambiguous — the sanitizer
-- re-brackets before storing (test-pinned). git ref/SHA provenance does NOT
-- exist in v1: it is a W7 decision, whose shape is to add this table's
-- ref columns THEN — which, under the never-update policy below, means every
-- row written before W7 lands is permanently NULL for them, with no backfill
-- this policy permits. Stated here as a decision, not a pending column.
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
-- requirement is "answer what the platform fetched for THIS bot, from where,
-- at that time"; until an audit horizon is named, any deletion is a
-- manufactured audit gap, and a cleanup that misjudges it is unrecoverable
-- by design. The blob layer is hence self-consistent: a digest's bytes exist
-- for as long as any row references them, and rows never go away. A
-- retention window, when audit names one, lands here as a comment change
-- plus a sweep mechanism — not as a silent default. THE ONE SANCTIONED
-- EXCEPTION, so it is stated rather than discovered: the blob layer's own
-- ``.tmp-*`` staging files from crashed writes are not audit facts and are
-- age-swept at store time (see content/service.py); they are the only thing
-- a sweeper may ever touch.
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
--   * ``TIMESTAMP`` FOR THE DB-CLOCK COLUMNS, ``DATETIME`` FOR THE REST, and
--     which one a column gets is decided by who fills it, not by taste.
--     gmt_create and gmt_modified are written by the database itself
--     (``DEFAULT CURRENT_TIMESTAMP``, and ``func.now()`` from the ORM), so
--     TIMESTAMP's session-time-zone conversion is a no-op round trip and they
--     match ac_bots and every table added since --
--     skill_center/sql/2026_09_03_align_space_skill_timestamps_with_gmt.sql is
--     the repair that convention exists to avoid repeating.
--
--     A column the APPLICATION fills stays DATETIME. TIMESTAMP reads the naive
--     value being bound as session-local wall time and converts it to UTC for
--     storage, so a Python-supplied instant is stored shifted by the session
--     offset -- eight hours under the Asia/Shanghai session assumed here. This
--     was got wrong in the first draft of this change and caught in review; fetched_at
--     is the column in question here, and its own comment below says why.
--
--     The ORM keeps ``DateTime`` for both kinds -- ac_skill_version's
--     published_at is the precedent for ``DateTime`` over a TIMESTAMP column.
--   * NO ``ENGINE`` CLAUSE, and no BLOCK_SIZE / REPLICA_NUM / COMPRESSION /
--     TABLET_SIZE / PCTFREE / ROW_FORMAT. OceanBase applies its own defaults
--     and echoes them back from SHOW CREATE TABLE; writing them here would be
--     copying its own output back at it.
--   * ``AUTO_INCREMENT_MODE = 'ORDER'``, pinned rather than inherited, because
--     id is the tie-break keep_last resolves on: reads order by
--     ``gmt_create DESC, id DESC``, so within one second the newest receipt is
--     the largest id -- true only while ids are allocated in order.

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
  -- nullable: an inline content_type header is optional on the wire, and
  -- advisory either way — a header wider than the column stores NULL plus
  -- a log line rather than refusing the whole receipt (the digest is the
  -- reconciliation anchor, not this).
  `content_type`  varchar(256)  DEFAULT NULL COMMENT '响应 Content-Type（advisory；超宽存 NULL）',
  `size_bytes`    bigint(20) unsigned NOT NULL COMMENT '字节数（与 digest 同为对账锚）',
  -- DATETIME, not TIMESTAMP, and deliberately out of step with the gmt_*
  -- columns below: content/service.py normalises FetchedObject.fetched_at to
  -- UTC and strips tzinfo before binding it. TIMESTAMP would read that
  -- already-UTC value as session-local wall time and store it shifted by the
  -- session offset -- eight hours, under the Asia/Shanghai session the gmt_*
  -- columns assume. The audit question this table exists to answer is *when*
  -- a bot received something, so a silently shifted instant is the one defect
  -- it cannot carry.
  `fetched_at`    datetime      NOT NULL COMMENT '拉取时间（FetchedObject.fetched_at；应用侧已归一到 UTC）',
  -- The join back to the apply record (ac_bot_config_manifest_apply), and
  -- the per-entry identity a fetch served: apply_id is what that table's
  -- own comment ("also what a per-entry table would join on") points at
  -- here; category + entry_identity are the entry's coordinates the same
  -- way EntryResult names them. All nullable because keep_last receipts
  -- predate entries (a receipt may be reused by an apply that declares
  -- the entry differently) and because the fetch pipeline, not the store,
  -- owns knowing them. Added NOW, while the table is empty: under the
  -- never-update retention below, a column added after rows exist is
  -- permanently NULL for all of them — there is no backfill this policy
  -- permits, and a provenance row that cannot say which apply or which
  -- entry it was fetched for answers neither of its own audit questions.
  `apply_id`      varchar(64)   DEFAULT NULL COMMENT '触发拉取的 apply（W4 报告键；干跑为 NULL）',
  `category`      varchar(32)   DEFAULT NULL COMMENT '条目类目（skills/identity/…；条目维度之一）',
  `entry_identity` varchar(256) DEFAULT NULL COMMENT '条目标识（对账锚外的维度：skill name / identity type / …）',
  `modifier`      varchar(1024) NOT NULL DEFAULT '' COMMENT '审计：触发拉取的身份',
  `gmt_create`    timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The audit query shape: one bot's receipts, newest first. Leading column
  -- tenant keeps it aligned with the guard's filter.
  KEY `idx_tenant_env_entity_bot`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `gmt_create`) GLOBAL,
  -- "What did apply X fetch" — the join the apply record's own comment
  -- anticipated this table providing.
  KEY `idx_apply` (`apply_id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'manifest 拉取内容平台副本的溯源日志（行 append-only，字节在内容寻址 blob 目录）';
