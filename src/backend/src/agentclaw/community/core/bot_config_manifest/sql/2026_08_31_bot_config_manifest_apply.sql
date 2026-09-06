-- The manifest apply record and its serialization lock (issue #1472, W4 of the
-- bot-config-manifest plan in ``docs/bot-config-manifest/``).
--
-- Two tables, one mechanism: an apply takes the lock, writes its RUNNING row,
-- does the work, and stamps the terminal row. Both carry the same logical key
-- as ac_bot_config_manifest and the same column widths, for the same reasons.
--
-- WHAT THE APPLY RECORD IS FOR. work-items §2.3: manifest-materialised entities
-- are stored identically to hand-created ones — same service, same table, same
-- shape — so nothing downstream can tell them apart, and nothing needs to. The
-- manifest module therefore keeps its OWN account of what it materialised,
-- rather than marking entities. That record serves audit and keep_last (§2.8),
-- and per §2.7 the per-entry details ARE the report: apply has no other output,
-- and it writes nothing at all to ac_bots.
--
-- WHY THE LOCK IS ITS OWN TABLE AND NOT A ROW IN ac_bot_restart_lock. Applying
-- a manifest and restarting a bot are different operations. Sharing a row would
-- make a restart block an apply (and vice versa) as an accident of storage
-- rather than as a decision anybody made. The *pattern* is reused verbatim —
-- the UNIQUE constraint is the lock, the DB arbitrates concurrent inserts, the
-- fencing token is compared on release, staleness is judged on the DB clock —
-- which is what work-items §5 asks for.
--
-- INDEX BUDGET, same as the manifest table's. InnoDB caps an index key at 3072
-- bytes and utf8mb4 counts 4 bytes per character, so entity_id is varchar(256)
-- and NOT ac_bots' 1024 — at 1024 that one column would be 4096 bytes and
-- CREATE TABLE would be refused outright. Real entity_ids are short user ids
-- (u_165137); 256 matches what the newer tables here give one.
--
-- TENANT ISOLATION, same as the manifest table's. ac_bots is itself
-- tenant-scoped, so a bot_id is unique only *within* a tenant, and legacy
-- "default" bots carry documented residual cross-tenant collision on that
-- identifier. Without the tenant in these keys, two such bots would share one
-- apply lock — so one tenant's apply could block or unblock another's — and
-- read each other's apply reports.

-- DIALECT: OCEANBASE, MYSQL MODE. What this file writes follows the deployed
-- tables there rather than portable MySQL -- compare ``SHOW CREATE TABLE
-- ac_bots``, and the sibling DDL in core/caller_identity/sql/.
--
--   * EVERY INDEX IS DECLARED ``GLOBAL``. On a partitioned table an index
--     without it is partition-local, and a partition-local UNIQUE enforces
--     uniqueness only *within* a partition. For ``uk_manifest_apply_lock``
--     that is the whole mechanism -- the UNIQUE constraint *is* the lock,
--     so a partition-local one lets two applies hold it at once. These tables are not
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
--     was got wrong in the first draft of this change and caught in review; started_at and
--     finished_at are the columns in question here, per their comment below.
--
--     The ORM keeps ``DateTime`` for both kinds -- ac_skill_version's
--     published_at is the precedent for ``DateTime`` over a TIMESTAMP column.
--   * NO ``ENGINE`` CLAUSE, and no BLOCK_SIZE / REPLICA_NUM / COMPRESSION /
--     TABLET_SIZE / PCTFREE / ROW_FORMAT. OceanBase applies its own defaults
--     and echoes them back from SHOW CREATE TABLE; writing them here would be
--     copying its own output back at it.
--   * ``AUTO_INCREMENT_MODE = 'ORDER'``, pinned rather than inherited from the
--     tenant default, because this table's read order rests on it:
--     ``config_manifest_apply.py`` answers GET .../last-apply with
--     ``ORDER BY id DESC``. Under NOORDER each observer caches its own
--     auto-increment range, so ids stop being monotonic in insertion order and
--     "max id" stops meaning "newest" -- last-apply would serve a stale report
--     while the newer one sat behind a smaller id.

CREATE TABLE `ac_bot_config_manifest_apply` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  -- The report's public identity: returned by the 202 from POST .../apply and
  -- polled on by GET .../applies/{apply_id}.
  --
  -- NOT a lookup key on its own. Both reads below filter on the bot key as
  -- well, so an apply_id guessed or leaked from another bot resolves to
  -- nothing. The id is a handle the caller polls with; it never authorizes the
  -- read. That is why there is no UNIQUE KEY on apply_id alone.
  `apply_id`       varchar(64)   NOT NULL COMMENT 'Public handle for this apply',
  `env`            varchar(20)   NOT NULL COMMENT 'Environment: prod/pre/dev',
  -- 256, not ac_bots' 1024 — see the index-budget note above.
  `entity_id`      varchar(256)  NOT NULL COMMENT 'Entity id: the bot entity_id',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  -- 'explicit' is the only value this wave writes: W4's single entry point is
  -- the explicit POST. W8 adds 'republish'/'restart' and W13 adds 'create',
  -- neither of which needs a migration.
  `trigger`        varchar(32)   NOT NULL COMMENT 'What started it: explicit/create/republish/restart',
  -- RUNNING on insert, terminal on completion — the two-write lifecycle apply's
  -- async shape requires, since the route answers 202 and the work continues on
  -- a background thread.
  --
  -- Denormalised out of `report` so "show me failed applies" is a query rather
  -- than a scan of JSON, and so a poll is one indexed read.
  `status`         varchar(16)   NOT NULL COMMENT 'RUNNING, or SUCCEEDED/PARTIAL/FAILED',
  -- mediumtext, not text: a report over a large manifest has no small cap to
  -- lean on, and text's 65,535 bytes is close enough to matter. Same divergence
  -- ac_bot_config_manifest.document records.
  `report`         mediumtext    NOT NULL COMMENT 'The per-entry report (JSON)',
  -- Bounded the way the manifest's `modifier` is, and for the same reason: an
  -- application actor composes a prefix onto a 1024-character user id
  -- ("app:7:on-behalf-of:<...>"), so the composed value can legitimately be
  -- long without anything being malformed.
  `actor`          varchar(1024) NOT NULL COMMENT 'Audit: who started it',
  -- DATETIME, not TIMESTAMP, for both of these: they are filled by the
  -- application from datetime.now() (process-local, naive), never by the
  -- database. TIMESTAMP binds a naive value as session-local, so these would
  -- be stored correctly only where the process time zone happens to equal the
  -- database session's -- and a container on UTC against an Asia/Shanghai
  -- session is exactly where that stops being true. gmt_* below are a
  -- different case: the database fills those itself.
  `started_at`     datetime      NOT NULL COMMENT 'When the apply began',
  -- NULL exactly while status is RUNNING. The two move together.
  `finished_at`    datetime      NULL     COMMENT 'When it ended; NULL while RUNNING',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `gmt_create`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Row created',
  `gmt_modified`   timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Row last modified',
  PRIMARY KEY (`id`),
  -- Two indexes, one per read. There are exactly two reads on this table.
  --
  -- GET .../last-apply — the newest row for this bot.
  KEY `idx_manifest_apply_latest`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `id`) GLOBAL,
  -- GET .../applies/{apply_id} — the poll by id, scoped to the bot.
  KEY `idx_manifest_apply_by_id`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `apply_id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot config manifest apply record';

-- NO dry_run COLUMN, deliberately. A dry run mints no apply_id and writes no
-- row at all, so there is nothing here to mark. A flag would invite a future
-- change to record plans by setting it, which is the write dry_run promises not
-- to make; the test asserts this table is untouched by a dry run instead.

CREATE TABLE `ac_bot_config_manifest_apply_lock` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `env`            varchar(20)   NOT NULL COMMENT 'Environment: prod/pre/dev',
  `entity_id`      varchar(256)  NOT NULL COMMENT 'Entity id: the bot entity_id',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  `holder_user_id` varchar(1024) NOT NULL COMMENT 'Lock holder (whoever started the apply)',
  -- Fencing token, compared on release. Without it, an apply whose lock was
  -- reaped as stale could delete the lock a *later* apply legitimately took.
  `lock_token`     varchar(256)  NOT NULL COMMENT 'Fencing token, compared on release',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `gmt_create`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Row created',
  `gmt_modified`   timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Row last modified',
  PRIMARY KEY (`id`),
  -- THE UNIQUE CONSTRAINT IS THE LOCK. One row per bot; concurrent INSERTs are
  -- arbitrated by the database, exactly one wins, and the losers see the
  -- integrity violation as "held". No application-side mutual exclusion.
  --
  -- gmt_create is what get_if_stale measures against, on the DB clock, so a
  -- process killed mid-apply cannot hold this forever.
  UNIQUE KEY `uk_manifest_apply_lock`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot config manifest apply serialization lock';
