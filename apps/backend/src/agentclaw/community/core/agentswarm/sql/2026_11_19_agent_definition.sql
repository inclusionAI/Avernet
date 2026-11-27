-- AgentDefinition and its run provenance.
--
-- Two tables, both additive. No existing table is altered and the run catalog
-- snapshot (`ac_agent_swarm_run.agent_catalog`) keeps its exact shape, which is
-- what keeps every already-stored run readable and makes rollback a plain
-- previous-image deploy. Forward-only doctrine (D38): this file only ever adds.
--
-- WHY `is_deleted` IS A TOMBSTONE ID ON THE DEFINITION TABLE.
-- A definition version is a row, and an edit inserts a successor rather than
-- mutating the incumbent - the `baas_bot` multi-version pattern
-- (`baas/src/secbaas/community/core/repository/bot/_orm_repository.py`). So
-- `is_deleted` is 0 for a live row and the row's own `id` once retired
-- ("0=正常, >0=归档记录ID"). That is what lets the unique keys below admit
-- exactly one live version per definition while retaining every retired one:
-- a retired row occupies a distinct slot because its `is_deleted` is a
-- non-zero id. The column is therefore bigint, not the sibling tables'
-- tinyint(1) - a boolean could not hold the value that makes this work.
--
-- WHY THERE IS NO `revision` COLUMN. `baas_bot` has none either; it orders and
-- addresses versions by `id`. `id` orders versions, `is_deleted` says which is
-- live, and provenance cites the exact row id. A separate counter would be a
-- second source of version truth that can disagree with `id` ordering.
--
-- WHY THE DIGEST CARRIES ITS ALGORITHM PREFIX. Stores `sha256:<64 lowercase
-- hex>`, the digest vocabulary fixed in
-- `core/bot_config_manifest/schema/_support.py` (`_DIGEST_RE`) and shared by
-- `content_store` / `bot_genome`. A bare hex string here would be unusable by
-- any consumer that expects that vocabulary. The column is varchar rather than
-- the siblings' char(71) so a future algorithm needs no column change; it is
-- never indexed, so the headroom costs nothing. Empty string means "no
-- manifest", exactly as `manifest_document` does.
--
-- WHY THE PROVENANCE TABLE KEYS ON THE CATALOG NAME, NOT THE SPAWNED AGENT.
-- It answers one question per catalog entry: which definition version did this
-- run's entry come from? One entry spawns agents named
-- `{catalog_name}_{agent_id[:12]}` and may do so several times; keying on the
-- instance would duplicate the same fact per spawn and would leave runs that
-- never spawned with no provenance at all. Catalog names are unique per run by
-- submission rule, which `uk_run_catalog` makes a constraint. The instance
-- direction needs no stored key: `ac_agent_swarm_agent` already carries
-- `run_id` and the suffixed name.
--
-- DIALECT: OCEANBASE, MYSQL MODE. Index names are explicit because both table
-- names are long enough that auto-generated names would truncate.

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_agent_definition` (
    `id`                bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK; also the version handle',
    `definition_id`     varchar(128) NOT NULL               COMMENT 'stable definition identity, reused across versions',
    `name`              varchar(128) NOT NULL               COMMENT 'catalog name the orchestrator spawns under',
    `role`              varchar(512) NOT NULL DEFAULT ''    COMMENT 'short author-written role; folded into bot_desc',
    `instructions`      mediumtext         DEFAULT NULL     COMMENT 'agent instructions',
    `kind`              varchar(16)  NOT NULL DEFAULT 'llm' COMMENT 'llm|bot',
    `model`             varchar(128) DEFAULT NULL           COMMENT 'model override',
    `engine`            varchar(64)  DEFAULT NULL           COMMENT 'teclaw|openclaw|claude_code',
    `bot_type`          varchar(32)  NOT NULL DEFAULT 'personal' COMMENT 'personal|...',
    `manifest_document` mediumtext         DEFAULT NULL     COMMENT 'bot_config_manifest YAML, stored verbatim; empty = none',
    `manifest_digest`   varchar(128) NOT NULL DEFAULT ''    COMMENT 'sha256:<hex64> of manifest_document; empty = none',
    `owner_id`          varchar(64)  NOT NULL               COMMENT 'who may read/write this definition; immutable',
    `creator_id`        varchar(64)  NOT NULL               COMMENT 'who authored the definition; copied forward on each version',
    `modifier_id`       varchar(64)  NOT NULL               COMMENT 'who produced this version',
    `env`               varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`            varchar(64)  NOT NULL               COMMENT 'tenant',
    `is_deleted`        bigint(20)   NOT NULL DEFAULT 0     COMMENT 'tombstone: 0=live, >0=retiring row id',
    `gmt_create`        timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_def_live` (`env`,`tenant`,`definition_id`,`is_deleted`),
    UNIQUE KEY `uk_owner_name` (`env`,`tenant`,`owner_id`,`name`,`is_deleted`),
    KEY `idx_owner_live` (`env`,`tenant`,`owner_id`,`is_deleted`)
) DEFAULT CHARSET = utf8mb4 COMMENT='AgentDefinition — one immutable version of a user-owned agent definition (the reusable who)';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_run_agent_ref` (
    `id`                bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `run_id`            varchar(128) NOT NULL               COMMENT 'run that took the snapshot',
    `catalog_name`      varchar(128) NOT NULL               COMMENT 'AgentSpec.name; join key into agent_catalog',
    `definition_id`     varchar(128) NOT NULL               COMMENT 'AgentDefinition the entry came from',
    `definition_row_id` bigint(20)   NOT NULL               COMMENT 'the exact version (definition table id) captured at submit',
    `env`               varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`            varchar(64)  NOT NULL               COMMENT 'tenant',
    `is_deleted`        tinyint(1)   NOT NULL DEFAULT 0,
    `gmt_create`        timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_run_catalog` (`env`,`tenant`,`run_id`,`catalog_name`),
    KEY `idx_prov_run` (`env`,`tenant`,`run_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='RunAgentRef — which definition version one catalog entry came from (the lineage)';
