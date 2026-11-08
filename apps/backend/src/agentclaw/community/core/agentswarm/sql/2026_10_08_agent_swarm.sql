-- agentswarm ac_agent_swarm_* tables.
-- The ORM (core/agentswarm/repository registry) renders a plain unique
-- index; the BLOCK_SIZE/LOCAL/GLOBAL modifiers below are OceanBase-only and
-- not expressible in SQLAlchemy. Operator-provisioned in prod.

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_run` (
    `id`                    bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `run_id`                varchar(128) NOT NULL               COMMENT 'run uuid',
    `env`                   varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`                varchar(64)  NOT NULL               COMMENT 'tenant from the API request',
    `goal`                  mediumtext         NOT NULL               COMMENT 'run goal',
    `agent_catalog`         mediumtext         DEFAULT NULL           COMMENT 'caller agent catalog JSON',
    `provider`              varchar(64)  DEFAULT NULL            COMMENT 'provider label',
    `request_metadata`      mediumtext         DEFAULT NULL           COMMENT 'opaque request metadata, secrets stripped',
    `status`                varchar(32)  NOT NULL               COMMENT 'RunStatus',
    `termination_reason`    varchar(32)  DEFAULT NULL           COMMENT 'TerminationReason',
    `root_agent_id`         varchar(128) DEFAULT NULL,
    `final_answer`          mediumtext         DEFAULT NULL,
    `error`                 mediumtext         DEFAULT NULL,
    `active_idempotency_key` varchar(190) DEFAULT NULL          COMMENT 'active only while run is non-terminal (D29)',
    `owner_id`               varchar(64)  NOT NULL DEFAULT ''   COMMENT 'authenticated caller — provisioned bot personas are owned by this account',
    `model`                  varchar(128) NOT NULL DEFAULT ''   COMMENT 'run-level API parameter — chat-completion model',
    `started_at`            bigint(20)  DEFAULT NULL           COMMENT 'epoch ms',
    `ended_at`              bigint(20)  DEFAULT NULL           COMMENT 'epoch ms',
    `is_deleted`            tinyint(1)   NOT NULL DEFAULT 0     COMMENT 'logical delete',
    `gmt_create`            timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`          timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_run_id` (`env`,`tenant`,`run_id`) BLOCK_SIZE 16384 LOCAL,
    UNIQUE KEY `uk_run_idem` (`env`,`tenant`,`active_idempotency_key`),
    KEY `idx_run_status` (`env`,`tenant`,`status`,`gmt_modified`)
) DEFAULT CHARSET = utf8mb4 COMMENT='Run — one submission; lifecycle and outcome ledger (the what)';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_agent` (
    `id`                bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `agent_id`          varchar(128) NOT NULL               COMMENT 'agent uuid',
    `run_id`            varchar(128) NOT NULL               COMMENT 'parent run',
    `parent_agent_id`   varchar(128) DEFAULT NULL           COMMENT 'lineage parent',
    `depth`             int          NOT NULL DEFAULT 0      COMMENT 'lineage depth (root=0)',
    `name`              varchar(128) NOT NULL               COMMENT 'spawn-local handle',
    `role`              mediumtext         DEFAULT NULL           COMMENT 'LLM-generated task description for this spawned agent',
    `kind`              varchar(16)  NOT NULL               COMMENT 'llm|bot',
    `task_spec`         mediumtext         DEFAULT NULL           COMMENT 'TaskEnvelope JSON',
    `spawn_round`       int          NOT NULL DEFAULT 0,
    `status`            varchar(32)  NOT NULL               COMMENT 'AgentStatus',
    `attempts`          int          NOT NULL DEFAULT 0      COMMENT 'agent provisioning retry count',
    `idempotency_key`   varchar(190) NOT NULL               COMMENT 'stable per logical agent (reused on re-spawn)',
    `output`            mediumtext         DEFAULT NULL           COMMENT 'structured output JSON',
    `error`             mediumtext         DEFAULT NULL,
    `bot_id`            varchar(128) DEFAULT NULL           COMMENT 'bot persona id',
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
) DEFAULT CHARSET = utf8mb4 COMMENT='Agent — one registered DAG node; lineage, kind, persona binding (the who)';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_message` (
    `id`            bigint(20)    NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `message_id`    varchar(128)  NOT NULL               COMMENT 'message uuid',
    `run_id`        varchar(128)  NOT NULL,
    `agent_id`      varchar(128)  NOT NULL,
    `seq`           bigint(20)    NOT NULL               COMMENT 'per-agent monotonic sequence (epoch ms; overflows int32)',
    `role`          varchar(16)   NOT NULL               COMMENT 'system|user|assistant|tool|decision',
    `content`       mediumtext          DEFAULT NULL,
    `decision`      mediumtext          DEFAULT NULL           COMMENT ' Decision JSON when role=decision',
    `token_usage`   mediumtext          DEFAULT NULL           COMMENT '{prompt_tokens,completion_tokens,total}',
    `artifact_key`  varchar(512)  DEFAULT NULL           COMMENT 'artifact store key when content externalized',
    `env`          varchar(20)   NOT NULL,
    `tenant`       varchar(64)  NOT NULL,
    `is_deleted`    tinyint(1)   NOT NULL DEFAULT 0,
    `gmt_create`   timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_message` (`env`,`tenant`,`agent_id`,`seq`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_message_run_agent` (`env`,`tenant`,`run_id`,`agent_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='Message — one agent-authored event (decision/completion bodies; per-agent sharded store)';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_agent_result` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `result_id`       varchar(128) NOT NULL               COMMENT 'result uuid',
    `run_id`          varchar(128) NOT NULL,
    `source_agent_id` varchar(128) NOT NULL               COMMENT 'agent that produced this result',
    `payload`         mediumtext         DEFAULT NULL           COMMENT 'the ONE final result of one agent; XOR with artifact_key (D31)',
    `artifact_key`    varchar(512) DEFAULT NULL           COMMENT 'artifact store key for large results; XOR with payload',
    `routed_at`       bigint(20)  DEFAULT NULL            COMMENT 'epoch ms routed to orchestrator',
    `env`             varchar(20) NOT NULL,
    `tenant`          varchar(64) NOT NULL,
    `is_deleted`      tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create`     timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`    timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_result` (`env`,`tenant`,`result_id`) BLOCK_SIZE 16384 LOCAL,
    UNIQUE KEY `uk_result_agent` (`env`,`tenant`,`run_id`,`source_agent_id`),
    KEY `idx_result_run` (`env`,`tenant`,`run_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='AgentResult — the ONE final result delivered by one agent (uk-enforced; payload or artifact spill) — NOT per-attempt storage, NOT db-sharding';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_round` (
    `id`                  bigint(20)  NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `round_id`            varchar(128) NOT NULL,
    `run_id`              varchar(128) NOT NULL,
    `stage_index`         int         NOT NULL,
    `orchestrator_steps`  int         NOT NULL DEFAULT 0      COMMENT 'orchestrator step count',
    `slowest_attempt_steps` int     NOT NULL DEFAULT 0      COMMENT 'slowest per-attempt executor steps',
    `cohort_agent_ids`    mediumtext        DEFAULT NULL           COMMENT 'JSON list of AgentIds in this round',
    `started_at`          bigint(20) DEFAULT NULL,
    `ended_at`            bigint(20) DEFAULT NULL,
    `env`                varchar(20) NOT NULL,
    `tenant`             varchar(64) NOT NULL,
    `is_deleted`          tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create`         timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`        timestamp  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_round` (`env`,`tenant`,`run_id`,`stage_index`) BLOCK_SIZE 16384 LOCAL
) DEFAULT CHARSET = utf8mb4 COMMENT='Round — one spawn cohort, its step accounting and cohort roster';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_guard_event` (
    `id`         bigint(20) NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `event_id`   varchar(128) NOT NULL,
    `run_id`     varchar(128) NOT NULL,
    `agent_id`   varchar(128) DEFAULT NULL,
    `kind`       varchar(32) NOT NULL               COMMENT 'GuardKind',
    `details`    mediumtext DEFAULT NULL                  COMMENT ' JSON details payload',
    `env`       varchar(20) NOT NULL,
    `tenant`    varchar(64) NOT NULL,
    `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_event` (`env`,`tenant`,`event_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_event_run_kind` (`env`,`tenant`,`run_id`,`kind`)
) DEFAULT CHARSET = utf8mb4 COMMENT='GuardEvent — one policy-reject or breaker evidence row (kind + details)';

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_run_dispatch` (
    `id`               bigint(20)  NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `dispatch_id`      varchar(128) NOT NULL,
    `run_id`           varchar(128) NOT NULL               COMMENT 'exactly one dispatch per run',
    `status`           varchar(20) NOT NULL               COMMENT 'READY|LEASED|DONE|FAILED',
    `ready_at`         timestamp   NOT NULL               COMMENT 'next eligible (DB clock)',
    `claimed_by`       varchar(128) DEFAULT NULL           COMMENT 'current worker holding the slice',
    `claim_epoch`      bigint(20)  NOT NULL DEFAULT 0     COMMENT 'monotonic fencing token, incremented on every claim',
    `lease_expires_at` timestamp   DEFAULT NULL           COMMENT 'expired => reclaim',
    `claims`           int         NOT NULL DEFAULT 0     COMMENT 'claim count, diagnostic only',
    `reclaims`         int         NOT NULL DEFAULT 0      COMMENT 'no-progress reclaim count, D40 circuit-breaker input',
    `progress_marker`  bigint(20)  NOT NULL DEFAULT -1    COMMENT 'routed-result count observed at last claim/reclaim; the sentinel -1 is the unobserved baseline — a first observation becomes the baseline, never counts as progress, so mixed-version fleets coexist safely (D40 progress-aware breaker)',
    `deadline_at`      timestamp   NOT NULL               COMMENT 'give-up time',
    `last_error`       mediumtext        DEFAULT NULL,
    `cancel_requested` tinyint(1)  NOT NULL DEFAULT 0     COMMENT 'durable cancel flag, polled by the slice loop',
    `env`             varchar(20) NOT NULL,
    `tenant`           varchar(64) NOT NULL,
    `is_deleted`       tinyint(1) NOT NULL DEFAULT 0,
    `gmt_create`       timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_dispatch_run` (`env`,`tenant`,`run_id`) BLOCK_SIZE 16384 GLOBAL,
    KEY `idx_dispatch_scope_status_ready_at` (`env`,`tenant`,`status`,`ready_at`),
    KEY `idx_dispatch_scope_lease` (`env`,`tenant`,`lease_expires_at`)
) DEFAULT CHARSET = utf8mb4 COMMENT='RunDispatch — exactly one pending/leased dispatch per run; hosts the claim fence and slice resumability (D34)';

-- agentswarm append-only audit table `ac_agent_swarm_audit` (design §17.5, D25).
-- One row per boundary outcome on /openapi/v1/agentswarm: submit_accepted,
-- submit_rejected (reason: tenant_limit/global_limit/queue_depth/config_missing),
-- cancel_requested (reason: not_found when the tenant sees no such run).
-- Append-only: no UPDATE/DELETE path exists (writer has no mutation API).
-- The ORM renders plain indexes; BLOCK_SIZE/LOCAL modifiers are OceanBase-only
-- and not expressible in SQLAlchemy. Operator-provisioned in prod, dated
-- forward file per migration policy 12.3.

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_audit` (
    `id`            bigint(20)    NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `env`           varchar(20)   NOT NULL               COMMENT 'deployment env',
    `tenant`        varchar(64)    NOT NULL               COMMENT 'tenant from the authenticated principal',
    `actor`         varchar(128)   NOT NULL               COMMENT 'staffId of the calling principal',
    `action`        varchar(64)    NOT NULL               COMMENT 'submit_accepted|submit_rejected|cancel_requested',
    `reason`        varchar(255)   DEFAULT NULL           COMMENT 'tenant_limit|global_limit|queue_depth|config_missing|not_found',
    `run_id`        varchar(128)   DEFAULT NULL           COMMENT 'target run when the outcome is run-specific',
    `source`        varchar(32)    NOT NULL DEFAULT 'http' COMMENT 'entry surface (http|cli-over-http)',
    `is_deleted`    tinyint(1)     NOT NULL DEFAULT 0     COMMENT 'logical delete (never set: append-only)',
    `gmt_create`    timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`  timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_audit_scope` (`env`,`tenant`,`id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_audit_run` (`env`,`tenant`,`run_id`),
    KEY `idx_audit_actor` (`env`,`tenant`,`actor`)
) DEFAULT CHARSET = utf8mb4 COMMENT='BoundaryAudit — one submit/cancel outcome row at the HTTP boundary (append-only action+reason, D25)';

-- ─────────────────────────────────────────────────────────────────────────
-- AgentAttempt ledger — compacted from the former 2026_10_17 file (D13
-- in-place venue: the module is pre-release, so no fleet ever applied the
-- dated files separately). Vocabulary note: the bare `attempt` column was
-- renamed `slice_retry_index` — it is the slice-local retry position that
-- mints the per-attempt persona identity (same digest triple).
--
-- Grain: one row per attempt PER SLICE — the UK carries the slice's claim
-- epoch, so a crash-resume replay of one attempt number is a NEW row and
-- the crashed slice's journal never depends on its write surviving the
-- replay (durable per-attempt persona identity, D-J/D-K).
-- executor_kind='bot' rows carry the minted platform identity
-- (ac_{sha1(run:agent:attempt)[:16]}); executor_kind='llm' rows carry the
-- bare per-attempt digest with no platform binding. DESTROYED is written
-- only by the terminal destroy sweep; llm rows stay SPAWNED and are
-- skipped by the sweep.

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_agent_attempt` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `agent_id`        varchar(128) NOT NULL               COMMENT 'agent uuid',
    `run_id`          varchar(128) NOT NULL               COMMENT 'parent run',
    `claim_epoch`     bigint(20)   NOT NULL DEFAULT 0     COMMENT 'slice claim epoch at journal time (D28 fence)',
    `slice_retry_index` int       NOT NULL DEFAULT 1     COMMENT 'in-slice retry index that mints the per-attempt persona identity (D-J)',
    `executor_kind`   varchar(16)  NOT NULL DEFAULT 'bot' COMMENT 'bot|llm',
    `executor_id`     varchar(64)  NOT NULL               COMMENT 'bot: minted ac_ persona id; llm: bare per-attempt digest (no platform binding)',
    `executor_name`   varchar(256) NOT NULL               COMMENT 'personas display name {spec.name}-{digest8}',
    `binding_state`   varchar(32)  NOT NULL DEFAULT 'SPAWNED' COMMENT 'SPAWNED|BOUND|DESTROYED binding lifecycle only — DESTROYED written only by the terminal sweep; llm rows stay SPAWNED; outcomes live in result_status, never here',
    `result_status`   varchar(32)  DEFAULT NULL           COMMENT 'per-attempt outcome: COMPLETED|FAILED|SLICE_LOST|TIMEOUT (diagnostic; binding_state is binding-only, never an outcome)',
    `result_message`  mediumtext   DEFAULT NULL           COMMENT 'failure reason for the attempt outcome',
    `started_at`      bigint(20)   DEFAULT NULL           COMMENT 'epoch ms journaled at spawn',
    `ended_at`        bigint(20)   DEFAULT NULL           COMMENT 'epoch ms (reserved; journal rows stay open by design)',
    `env`             varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`          varchar(64)  NOT NULL               COMMENT 'tenant',
    `is_deleted`      tinyint(1)   NOT NULL DEFAULT 0,
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_agent_attempt` (`env`,`tenant`,`run_id`,`agent_id`,`claim_epoch`,`slice_retry_index`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_attempt_run` (`env`,`tenant`,`run_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='AgentAttempt — one executor spawn per slice retry; the persona/binding journal (which materialization ran; outcome in result_status)';