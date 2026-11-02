-- Bot Genome Registry (work item RSI-03): the five tables behind
-- core/bot_genome. Design: docs/bot-evolution/01-genome.md §9; format:
-- api-contracts/genome/v1/README.md; ORM: core/bot_genome/repository/models.py
-- (the two must agree column for column; test-pinned).
--
-- Dialect notes, as for 2026_08_31_manifest_content.sql: OceanBase (MySQL
-- mode) is the production dialect; `GLOBAL` marks every secondary index as a
-- global index; the same ORM body runs on SQLite locally.
--
-- Every table carries the bot axes (avernet_tenant, env, entity_id, bot_id):
-- a bot_id is unique only within a tenant, entity_id is the storage key the
-- Manifest tables share (256 wide for the InnoDB key budget: utf8mb4 bills 4
-- bytes per character and a key is capped at 3072 bytes), and the tenant
-- column is stamped and filtered by the application's tenant guard.
--
-- Bytes never live here. Every file a revision names is in the manifest
-- content store, addressed by digest and tied to the bot by an
-- ac_manifest_content provenance row (produced://genome/* URLs).

-- One row per revision. `content` holds the exact hashed bytes
-- (JCS({"policy": …, "spec": …})), so a reader re-hashes it and proves the
-- id; spec and policy are parsed from it on read and never stored twice.
-- Rows are immutable except `status`, `evaluations` and `annotations`, which
-- are metadata outside the hash (README §3.2). `seq` is the per-bot
-- human-facing number (r42): MAX(seq)+1 inside the insert transaction, and a
-- lost race hits uk_bot_seq and retries. A hit on uk_bot_revision means the
-- content already exists: recording is idempotent (01-genome.md §4.4).
CREATE TABLE `ac_genome_revision` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `env`            varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`      varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `revision_id`    char(71)      NOT NULL COMMENT 'sha256 of JCS({policy, spec})',
  `seq`            int(11)       NOT NULL COMMENT 'per-bot sequence number, for humans (r42)',
  `parent_id`      char(71)      DEFAULT NULL COMMENT 'the revision this one was derived from',
  `genome_schema`  int(11)       NOT NULL COMMENT 'contract version (1)',
  `status`         varchar(16)   NOT NULL COMMENT 'draft/candidate/accepted/rejected/promoted/archived',
  `created_kind`   varchar(16)   NOT NULL COMMENT 'user/strategy_run',
  `created_actor`  varchar(1100) NOT NULL COMMENT 'who recorded it; an app acting for a user is app:<id>:on-behalf-of:<user>',
  `run_id`         varchar(64)   DEFAULT NULL COMMENT 'the evolution run, for strategy_run',
  `run_step`       varchar(64)   DEFAULT NULL COMMENT 'the run step',
  `patch_digest`   char(71)      DEFAULT NULL COMMENT 'the patch that produced it',
  `evidence`       text          NOT NULL COMMENT 'JSON list of evidence refs',
  `evaluations`    text          NOT NULL COMMENT 'JSON list of evaluation ids',
  `annotations`    text          NOT NULL COMMENT 'JSON object',
  `content`        mediumtext    NOT NULL COMMENT 'JCS({"policy":…,"spec":…}), the hashed bytes',
  `created_at`     datetime      NOT NULL COMMENT 'record time, UTC',
  `gmt_create`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`   timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_revision` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `revision_id`) GLOBAL,
  UNIQUE KEY `uk_bot_seq`      (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `seq`) GLOBAL,
  -- Lineage reads: the children of one revision.
  KEY `idx_genome_rev_bot_parent` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `parent_id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot Genome revisions: immutable, content-addressed versions of a bot definition';

-- One row per (bot, name): active, previous, canary, draft, or
-- candidate/<run>/<n>. A ref moves only by compare-and-swap on the revision
-- it points at (01-genome.md §5.1); `version` counts the moves. The
-- active/previous pair moves in one transaction.
CREATE TABLE `ac_genome_ref` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `env`            varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`      varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `name`           varchar(160)  NOT NULL COMMENT 'active/previous/canary/draft/candidate/<run>/<n>',
  `revision_id`    char(71)      NOT NULL COMMENT 'where the ref points',
  `version`        bigint(20)    NOT NULL DEFAULT 1 COMMENT 'moves so far; CAS token',
  `moved_at`       datetime      NOT NULL,
  `moved_kind`     varchar(16)   NOT NULL COMMENT 'user/strategy_run',
  `moved_actor`    varchar(1100) NOT NULL,
  `run_id`         varchar(64)   DEFAULT NULL,
  `reason`         varchar(1024) DEFAULT NULL,
  `labels`         varchar(256)  NOT NULL DEFAULT '[]' COMMENT 'JSON list of strings; today only "partial", on active or previous',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_ref` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `name`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot Genome refs: named pointers at revisions, moved by compare-and-swap';

-- The append-only move log: one row per ref move, never updated or deleted.
CREATE TABLE `ac_genome_ref_event` (
  `id`               bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `env`              varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`        varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`           varchar(256)  NOT NULL COMMENT 'Bot ID',
  `avernet_tenant`   varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `name`             varchar(160)  NOT NULL,
  `from_revision_id` char(71)      DEFAULT NULL COMMENT 'NULL when the ref was created',
  `to_revision_id`   char(71)      NOT NULL,
  `moved_kind`       varchar(16)   NOT NULL,
  `moved_actor`      varchar(1100) NOT NULL,
  `run_id`           varchar(64)   DEFAULT NULL,
  `reason`           varchar(1024) DEFAULT NULL,
  `moved_at`         datetime      NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_genome_ref_event_bot_ref` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `name`, `moved_at`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot Genome ref moves, append-only';

-- Recorded patches, content-addressed: the digest of the canonical JSON is
-- the candidate id (README §3.3), so recording the same patch twice is one row.
CREATE TABLE `ac_genome_patch` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `env`            varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`      varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `digest`         char(71)      NOT NULL COMMENT 'sha256 of JCS(patch): the candidate id',
  `base_id`        char(71)      NOT NULL COMMENT 'the revision the patch was written against',
  `content`        mediumtext    NOT NULL COMMENT 'JCS(patch)',
  `created_at`     datetime      NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_patch` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `digest`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot Genome patches, content-addressed';

-- One row per promotion: which revision it delivers and which ref it moves
-- once the delivery ends, who asked and why, and how the delivery went
-- (pending → applying → applied / applied_partial / apply_failed). The
-- revision is the record of what was delivered, so the row carries no copy
-- of it; whether the bot still holds `active` is observed by reading the
-- bot, never joined from here.
CREATE TABLE `ac_genome_promotion` (
  `id`               bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `env`              varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`        varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`           varchar(256)  NOT NULL COMMENT 'Bot ID',
  `avernet_tenant`   varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `promotion_id`     char(32)      NOT NULL COMMENT 'uuid4 hex',
  `ref`              varchar(16)   NOT NULL COMMENT 'active/canary',
  `revision_id`      char(71)      NOT NULL,
  `from_revision_id` char(71)      DEFAULT NULL COMMENT 'where the ref pointed before',
  `going_back`       tinyint(1)    NOT NULL COMMENT 'the target had been promoted before',
  `actor_kind`       varchar(16)   NOT NULL COMMENT 'user/strategy_run',
  `actor_id`         varchar(1100) NOT NULL,
  `actor_run_id`     varchar(64)   DEFAULT NULL COMMENT 'the evolution run, for strategy_run',
  `reason`           varchar(1024) NOT NULL,
  `status`           varchar(16)   NOT NULL COMMENT 'pending/applying/applied/applied_partial/apply_failed',
  `apply_id`         varchar(32)   DEFAULT NULL COMMENT 'the apply run delivering it, once started',
  `apply_detail`     varchar(1024) NOT NULL DEFAULT '' COMMENT 'one line on how the delivery went',
  `created_at`       datetime      NOT NULL,
  `settled_at`       datetime      DEFAULT NULL COMMENT 'when the delivery ended and the promotion took its final status',
  `gmt_create`       timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_genome_promotion` (`avernet_tenant`, `promotion_id`) GLOBAL,
  KEY `idx_genome_promotion_bot_created` (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `created_at`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot Genome promotions: the delivery a promotion started and the refs it moved';
