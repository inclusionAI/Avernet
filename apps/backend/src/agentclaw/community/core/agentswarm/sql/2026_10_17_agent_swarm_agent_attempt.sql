-- agentswarm per-attempt executor ledger `ac_agent_swarm_agent_attempt`.
-- Dated after 2026_10_16_agent_swarm_progress_marker.sql and applied
-- ascending by file name (the prior 2026_10_16 prefix is taken; the
-- schema-contract suite forbids a duplicate date prefix). Additive,
-- backward-readable (D38): no existing table is touched and old
-- replicas never read the new one. Mirrors
-- repository/agent_attempt/model.py and the simulator
-- agentclawdb-structure.sql.
--
-- Grain: one row per attempt PER SLICE — the UK carries the slice's
-- claim epoch, so a crash-resume replay of one attempt number is a NEW
-- row and the crashed slice's journal never depends on its write
-- surviving the replay (durable per-attempt persona identity, D-J/D-K).
-- executor_kind='bot' rows carry the minted platform identity
-- (ac_{sha1(run:agent:attempt)[:16]}); executor_kind='llm' rows carry
-- the bare per-attempt digest with no platform binding. DESTROYED is
-- written only by the terminal destroy sweep; llm rows stay SPAWNED and
-- are skipped by the sweep.

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_agent_attempt` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `agent_id`        varchar(128) NOT NULL               COMMENT 'agent uuid',
    `run_id`          varchar(128) NOT NULL               COMMENT 'parent run',
    `claim_epoch`     bigint(20)   NOT NULL DEFAULT 0     COMMENT 'slice claim epoch at journal time (D28 fence)',
    `attempt`         int          NOT NULL DEFAULT 1     COMMENT 'in-slice retry index (per-attempt persona identity)',
    `executor_kind`   varchar(16)  NOT NULL DEFAULT 'bot' COMMENT 'bot|llm',
    `executor_id`     varchar(64)  NOT NULL               COMMENT 'bot: minted ac_ persona id; llm: bare per-attempt digest (no platform binding)',
    `executor_name`   varchar(128) NOT NULL               COMMENT 'personas display name {spec.name}-{digest8}',
    `executor_state`  varchar(32)  NOT NULL DEFAULT 'SPAWNED' COMMENT 'SPAWNED|DESTROYED — DESTROYED written only by the terminal sweep; llm rows stay SPAWNED',
    `started_at`      bigint(20)   DEFAULT NULL           COMMENT 'epoch ms journaled at spawn',
    `ended_at`        bigint(20)   DEFAULT NULL           COMMENT 'epoch ms (reserved; journal rows stay open by design)',
    `env`             varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`          varchar(64)  NOT NULL               COMMENT 'tenant',
    `is_deleted`      tinyint(1)   NOT NULL DEFAULT 0,
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_agent_attempt` (`env`,`tenant`,`run_id`,`agent_id`,`claim_epoch`,`attempt`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_attempt_run` (`env`,`tenant`,`run_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentswarm per-attempt executor ledger';