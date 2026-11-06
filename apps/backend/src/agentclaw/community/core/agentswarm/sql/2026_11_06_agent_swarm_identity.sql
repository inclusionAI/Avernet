-- agentswarm run identity: one AgentPass per run, durable across restarts.
--
-- Without this row a restarted process would treat an issued identity as absent
-- and apply for it again: the swarm bot id is derived from the run id, so the
-- second apply addresses the same Passport key the service already honoured.
--
-- The token is never stored here — it is fetched on demand, the same rule every
-- other Passport surface follows. `worker_count` is a shape signal, not a
-- refcount: destruction is decided once at run end, not when the count hits zero.
-- OceanBase-only index modifiers below are not expressible in SQLAlchemy.
-- Operator-provisioned in prod.

CREATE TABLE IF NOT EXISTS `ac_agent_swarm_identity` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT 'PK',
    `run_id`          varchar(128) NOT NULL               COMMENT 'run uuid — the registry key',
    `env`             varchar(20)  NOT NULL               COMMENT 'deployment env',
    `tenant`          varchar(64)  NOT NULL               COMMENT 'tenant from the API request',
    `swarm_bot_id`    varchar(190) NOT NULL               COMMENT 'the bot the run minted; the AgentPass is addressed by it',
    `agent_code`      varchar(190) NOT NULL               COMMENT 'agent code returned by the single application',
    `status`          varchar(32)  NOT NULL DEFAULT 'ISSUED' COMMENT 'ISSUED|DESTROYED',
    `mcp_codes`       mediumtext       DEFAULT NULL           COMMENT 'union of persona MCP codes, JSON array',
    `cli_items`       mediumtext       DEFAULT NULL           COMMENT 'union of persona CLI items, JSON array',
    `worker_count`    int          NOT NULL DEFAULT 0      COMMENT 'workers bound to this identity (shape signal, not a refcount)',
    `owner_id`        varchar(64)  NOT NULL DEFAULT ''     COMMENT 'authenticated owner the identity was applied for',
    `is_deleted`      tinyint(1)   NOT NULL DEFAULT 0      COMMENT 'logical delete',
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_swarm_identity_run` (`env`,`tenant`,`run_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_swarm_identity_bot` (`env`,`tenant`,`swarm_bot_id`),
    KEY `idx_swarm_identity_status` (`env`,`tenant`,`status`,`gmt_modified`)
) DEFAULT CHARSET = utf8mb4 COMMENT='One AgentPass per swarm run; lets a restart resume instead of re-applying';