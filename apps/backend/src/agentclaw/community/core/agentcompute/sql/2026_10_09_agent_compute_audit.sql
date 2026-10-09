-- agentcompute append-only audit table `ac_agent_compute_audit` (design §17.5, D25).
-- One row per boundary outcome on /agentcompute/v1: submit_accepted,
-- submit_rejected (reason: tenant_limit/global_limit/queue_depth/config_missing),
-- cancel_requested (reason: not_found when the tenant sees no such run).
-- Append-only: no UPDATE/DELETE path exists (writer has no mutation API).
-- The ORM renders plain indexes; BLOCK_SIZE/LOCAL modifiers are OceanBase-only
-- and not expressible in SQLAlchemy. Operator-provisioned in prod, dated
-- forward file per migration policy 12.3.

CREATE TABLE IF NOT EXISTS `ac_agent_compute_audit` (
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
) DEFAULT CHARSET = utf8mb4 COMMENT='agentcompute append-only boundary audit';