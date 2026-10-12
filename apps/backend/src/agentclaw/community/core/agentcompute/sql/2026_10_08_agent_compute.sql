-- agentcompute ac_agent_compute_* tables.
-- The ORM (core/agentcompute/repository registry) renders a plain unique
-- index; the BLOCK_SIZE/LOCAL/GLOBAL modifiers below are OceanBase-only and
-- not expressible in SQLAlchemy. Operator-provisioned in prod.

CREATE TABLE IF NOT EXISTS `ac_agent_compute_run` (
    `id`                    bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `run_id`                varchar(128) NOT NULL               COMMENT 'run uuid',
    `env`                   varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`                varchar(64)  NOT NULL               COMMENT 'tenant from the API request',
    `goal`                  text         NOT NULL               COMMENT 'run goal',
    `agent_catalog`         text         DEFAULT NULL           COMMENT 'caller agent catalog JSON',
    `provider`              varchar(64)  DEFAULT NULL            COMMENT 'provider label',
    `request_metadata`      text         DEFAULT NULL           COMMENT 'opaque request metadata, secrets stripped',
    `status`                varchar(32)  NOT NULL               COMMENT 'RunStatus',
    `termination_reason`    varchar(32)  DEFAULT NULL           COMMENT 'TerminationReason',
    `root_agent_id`         varchar(128) DEFAULT NULL,
    `final_answer`          text         DEFAULT NULL,
    `error`                 text         DEFAULT NULL,
    `active_idempotency_key` varchar(190) DEFAULT NULL          COMMENT 'active only while run is non-terminal (D29)',
    `started_at`            bigint(20)  DEFAULT NULL           COMMENT 'epoch ms',
    `ended_at`              bigint(20)  DEFAULT NULL           COMMENT 'epoch ms',
    `is_deleted`            tinyint(1)   NOT NULL DEFAULT 0     COMMENT 'logical delete',
    `gmt_create`            timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`          timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_run_id` (`env`,`tenant`,`run_id`) BLOCK_SIZE 16384 LOCAL,
    UNIQUE KEY `uk_run_idem` (`env`,`tenant`,`active_idempotency_key`),
    KEY `idx_run_status` (`env`,`tenant`,`status`,`gmt_modified`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute run';

CREATE TABLE IF NOT EXISTS `ac_agent_compute_agent` (
    `id`                bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `agent_id`          varchar(128) NOT NULL               COMMENT 'agent uuid',
    `run_id`            varchar(128) NOT NULL               COMMENT 'parent run',
    `parent_agent_id`   varchar(128) DEFAULT NULL           COMMENT 'lineage parent',
    `depth`             int          NOT NULL DEFAULT 0      COMMENT 'lineage depth (root=0)',
    `name`              varchar(128) NOT NULL               COMMENT 'spawn-local handle',
    `role`              varchar(256) DEFAULT NULL,
    `kind`              varchar(16)  NOT NULL               COMMENT 'llm|avernet',
    `task_spec`         text         DEFAULT NULL           COMMENT 'TaskEnvelope JSON',
    `spawn_round`       int          NOT NULL DEFAULT 0,
    `status`            varchar(32)  NOT NULL               COMMENT 'AgentStatus',
    `attempts`          int          NOT NULL DEFAULT 0      COMMENT 'subagent retry attempt count',
    `idempotency_key`   varchar(190) NOT NULL               COMMENT 'stable per logical subagent (reused on re-spawn)',
    `output`            text         DEFAULT NULL           COMMENT 'structured output JSON',
    `error`             text         DEFAULT NULL,
    `bot_id`            varchar(128) DEFAULT NULL           COMMENT 'avernet bot id',
    `bot_state`         varchar(32)  DEFAULT NULL           COMMENT 'BotState enum value',
    `started_at`        bigint(20)  DEFAULT NULL           COMMENT 'epoch ms',
    `ended_at`          bigint(20)  DEFAULT NULL           COMMENT 'epoch ms',
    `env`               varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`            varchar(64)  NOT NULL               COMMENT 'tenant',
    `is_deleted`        tinyint(1)   NOT NULL DEFAULT 0,
    `gmt_create`        timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_agent` (`env`,`tenant`,`run_id`,`agent_id`) BLOCK_SIZE 16384 LOCAL,
    UNIQUE KEY `uk_agent_idem` (`env`,`tenant`,`run_id`,`idempotency_key`),
    KEY `idx_agent_status` (`env`,`tenant`,`run_id`,`status`),
    KEY `idx_agent_parent` (`env`,`tenant`,`run_id`,`parent_agent_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute agent lineage';

CREATE TABLE IF NOT EXISTS `ac_agent_compute_message` (
    `id`            bigint(20)    NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `message_id`    varchar(128)  NOT NULL               COMMENT 'message uuid',
    `run_id`        varchar(128)  NOT NULL,
    `agent_id`      varchar(128)  NOT NULL,
    `seq`           int           NOT NULL               COMMENT 'per-agent monotonic sequence',
    `role`          varchar(16)   NOT NULL               COMMENT 'system|user|assistant|tool|decision',
    `content`       text          DEFAULT NULL,
    `decision`      text          DEFAULT NULL           COMMENT ' Decision JSON when role=decision',
    `token_usage`   text          DEFAULT NULL           COMMENT '{prompt_tokens,completion_tokens,total}',
    `artifact_key`  varchar(512)  DEFAULT NULL           COMMENT 'artifact store key when content externalized',
    `env`          varchar(20)   NOT NULL,
    `tenant`       varchar(64)  NOT NULL,
    `is_deleted`    tinyint(1)   NOT NULL DEFAULT 0,
    `gmt_create`   timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_message` (`env`,`tenant`,`agent_id`,`seq`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_message_run_agent` (`env`,`tenant`,`run_id`,`agent_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute per-agent sharded message';

CREATE TABLE IF NOT EXISTS `ac_agent_compute_result_shard` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `shard_id`        varchar(128) NOT NULL               COMMENT 'shard uuid',
    `run_id`          varchar(128) NOT NULL,
    `source_agent_id` varchar(128) NOT NULL               COMMENT 'subagent that produced this result',
    `payload`         text         DEFAULT NULL           COMMENT 'structured result; XOR with artifact_key (D31)',
    `artifact_key`    varchar(512) DEFAULT NULL           COMMENT 'artifact store key for large results; XOR with payload',
    `routed_at`       bigint(20)  DEFAULT NULL            COMMENT 'epoch ms routed to orchestrator',
    `env`             varchar(20) NOT NULL,
    `tenant`          varchar(64) NOT NULL,
    `is_deleted`      tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create`     timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`    timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_shard` (`env`,`tenant`,`shard_id`) BLOCK_SIZE 16384 LOCAL,
    UNIQUE KEY `uk_shard_agent` (`env`,`tenant`,`run_id`,`source_agent_id`),
    KEY `idx_shard_run` (`env`,`tenant`,`run_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute result shard (exactly one of payload XOR artifact_key)';

CREATE TABLE IF NOT EXISTS `ac_agent_compute_round` (
    `id`                  bigint(20)  NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `round_id`            varchar(128) NOT NULL,
    `run_id`              varchar(128) NOT NULL,
    `stage_index`         int         NOT NULL,
    `orchestrator_steps`  int         NOT NULL DEFAULT 0      COMMENT 'orchestrator step count',
    `max_subagent_steps` int         NOT NULL DEFAULT 0      COMMENT 'slowest subagent steps',
    `cohort_agent_ids`    text        DEFAULT NULL           COMMENT 'JSON list of AgentIds in this round',
    `started_at`          bigint(20) DEFAULT NULL,
    `ended_at`            bigint(20) DEFAULT NULL,
    `env`                varchar(20) NOT NULL,
    `tenant`             varchar(64) NOT NULL,
    `is_deleted`          tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create`         timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`        timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_round` (`env`,`tenant`,`run_id`,`stage_index`) BLOCK_SIZE 16384 LOCAL
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute round timeline';

CREATE TABLE IF NOT EXISTS `ac_agent_compute_guard_event` (
    `id`         bigint(20) NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `event_id`   varchar(128) NOT NULL,
    `run_id`     varchar(128) NOT NULL,
    `agent_id`   varchar(128) DEFAULT NULL,
    `kind`       varchar(32) NOT NULL               COMMENT 'GuardKind',
    `details`    text DEFAULT NULL                  COMMENT ' JSON details payload',
    `env`       varchar(20) NOT NULL,
    `tenant`    varchar(64) NOT NULL,
    `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_event` (`env`,`tenant`,`event_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_event_run_kind` (`env`,`tenant`,`run_id`,`kind`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute guard event audit';

CREATE TABLE IF NOT EXISTS `ac_agent_compute_job` (
    `id`               bigint(20)  NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `job_id`           varchar(128) NOT NULL,
    `run_id`           varchar(128) NOT NULL               COMMENT 'exactly one job per run',
    `status`           varchar(20) NOT NULL               COMMENT 'PENDING|RUNNING|SUCCEEDED|FAILED|TIMED_OUT',
    `run_at`           timestamp   NOT NULL               COMMENT 'next eligible (DB clock)',
    `claimed_by`       varchar(128) DEFAULT NULL           COMMENT 'current worker holding the slice',
    `claim_epoch`      bigint(20)  NOT NULL DEFAULT 0     COMMENT 'monotonic fencing token, incremented on every claim',
    `lease_expires_at` timestamp   DEFAULT NULL           COMMENT 'expired => reclaim',
    `attempts`        int         NOT NULL DEFAULT 0      COMMENT 'diagnostic only',
    `reclaims`        int         NOT NULL DEFAULT 0      COMMENT 'no-progress reclaim count, D40 circuit-breaker input',
    `deadline_at`      timestamp   NOT NULL               COMMENT 'give-up time',
    `last_error`       text        DEFAULT NULL,
    `cancel_requested` tinyint(1)  NOT NULL DEFAULT 0     COMMENT 'durable cancel flag, polled by the slice loop',
    `env`             varchar(20) NOT NULL,
    `tenant`          varchar(64) NOT NULL,
    `is_deleted`       tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create`       timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_job_run` (`env`,`tenant`,`run_id`) BLOCK_SIZE 16384 GLOBAL,
    KEY `idx_job_scope_status_run_at` (`env`,`tenant`,`status`,`run_at`),
    KEY `idx_job_scope_lease` (`env`,`tenant`,`lease_expires_at`)
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute resumable-slice queue (D34)';