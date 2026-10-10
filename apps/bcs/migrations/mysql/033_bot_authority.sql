-- 031_bot_authority.sql — bot owner/manager authority schema, complete in one
-- release (spec: docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md
-- §5 source encoding, §5.2 transfer fields, §5.3 uniqueness/indexes, §5.4
-- manager audit, §12.5 ordinary business action audit, §13.3 ownership
-- initialization). Applied externally (ops/CI); the bcs binary runs only the
-- SQLite chain. This migration is FROZEN once released: no column backfill
-- may ride on later migrations.
--
-- Object list (plan Task 2):
--   1. edge_grants: management_source_kind/id (NOT NULL), active owner slot,
--      unified unique key incl. grant_ref_id (roles are fixed ref 0, so the
--      key's dedup equals the spec's six role columns while permission
--      profile/rules keep the runtime-ref dimension), kind/source shape CHECK.
--   2. bcs_bots.ownership_version (NOT NULL, historical rows default 0).
--   3. bot_ownership_transfers: full §5.2 field set, pending slot via a
--      nullable generated column (no PostgreSQL partial-index syntax),
--      client_request_id idempotency key, received/sent inbox indexes.
--   4. bot_manager_changes: direct management mutation ledger.
--   5. bcs_bot_action_audits: §12.5 ordinary business action audit.
--   6. bcs_message_deliveries.operation_id / bcs_chat_runs.operation_id:
--      same-transaction admitted identity snapshot reference (historical
--      rows stay NULL, no fabricated context backfill).
--   7. bot_manager_sync_operations / 8. bot_team_manager_sources: team
--      manager sync receipts and current team binding state; an empty
--      manager snapshot still represents an active team binding.
--   9. bot_ownership_initializations: first-owner initialization ledger
--      (audit only, never a source of current ownership facts).
--
-- Encoding constants are the shared `bcs_domain` storage vocabulary
-- (crates/contracts/bcs-domain/src/bot_authority.rs); the bootstrap test
-- sql_files_share_the_domain_source_encoding_constants locks this file's
-- literals to those constants. No `runtime`/`legacy` constants may appear.
--
-- Composite index byte budget (utf8mb4 = 4 bytes/char, InnoDB limit 3072;
-- edge_grants keeps its existing env VARCHAR(16) = 64 bytes, the new tables
-- use env VARCHAR(64) = 256 bytes):
--   uk_edge_from_to_env_kind_ref_source  1024+1024+ 64+ 64+  8+128+256 = 2608
--   uk_edge_active_owner_slot               64+1024+   1                = 1089
--   uk_bot_transfer_client_request         256+1024+1024+256            = 2560
--   idx_transfer_received                 256+1024+  64+  4+256        = 1604
--   uk_bot_action_audit_id                2048 (audit_id VARCHAR(512))
--   uk_bot_action_audit_slot              256+1024+ 384               = 1664
--   idx_bot_action_audit_resource         256+ 128+2048+  4            = 2436
--   uk_manager_changes_audit_id           2048 (audit_id VARCHAR(512))
--   uk_ownership_init_audit_id            2048 (audit_id VARCHAR(512))
--   uk_manager_sync_scope            256+1024+1024+256+256        = 2816
-- Width budgets of the composite ledger ids are pinned to the shared
-- `bcs_domain` constants (bcs_domain::AUTHORITY_*_VARCHAR_WIDTH): audit_id
-- VARCHAR(512), operation_id VARCHAR(256), service_id VARCHAR(256)
-- (the credential verifier's MAX_CLAIM_LEN bound). Client-supplied ids
-- (team_id / idempotency_key / client_request_id / batch_id) stay
-- VARCHAR(64) and are length-validated by the application (fail-closed
-- 400, never a DB 1406): they participate in uk_manager_sync_scope/
-- uk_bot_transfer_client_request whose byte budgets leave no widening room.
-- All subject IDs compare by exact case-sensitive identity: new tables use
-- COLLATE utf8mb4_bin; edge_grants keeps its existing column collation and
-- the edge store's existing dialect encapsulation (spec §5.3).

-- === 1. edge_grants ==========================================================
ALTER TABLE `edge_grants`
  ADD COLUMN `management_source_kind` VARCHAR(32) NOT NULL DEFAULT 'none'
    COMMENT 'role edge management source kind: owner | direct | team | ownership_transfer; non-role edges fixed none',
  ADD COLUMN `management_source_id` VARCHAR(64) NOT NULL DEFAULT 'none'
    COMMENT 'role edge management source id: owner | manual | <team_id> | <transfer_id>; non-role edges fixed none';

ALTER TABLE `edge_grants`
  ADD COLUMN `active_owner_slot` TINYINT
    GENERATED ALWAYS AS (CASE WHEN `grant_kind` = 'owner' AND `status` = 'approved'
                              THEN 1 ELSE NULL END) STORED
    COMMENT 'nullable generated approved-owner slot (§5.3 MySQL variant)';

ALTER TABLE `edge_grants`
  DROP INDEX `uk_edge_from_to_env_ref`;

ALTER TABLE `edge_grants`
  ADD UNIQUE KEY `uk_edge_from_to_env_kind_ref_source`
    (`from_id`, `to_id`, `env`, `grant_kind`, `grant_ref_id`,
     `management_source_kind`, `management_source_id`),
  ADD UNIQUE KEY `uk_edge_active_owner_slot`
    (`env`, `to_id`, `active_owner_slot`);

ALTER TABLE `edge_grants`
  ADD CONSTRAINT `chk_edge_role_source` CHECK (
    (`grant_kind` IN ('permission_profile', 'rules')
       AND `management_source_kind` = 'none' AND `management_source_id` = 'none')
    OR (`grant_kind` = 'owner' AND `grant_ref_id` = 0 AND `rules` IS NULL
       AND `management_source_kind` = 'owner' AND `management_source_id` = 'owner')
    OR (`grant_kind` = 'manager' AND `grant_ref_id` = 0 AND `rules` IS NULL
       AND ((`management_source_kind` = 'direct' AND `management_source_id` = 'manual')
         OR (`management_source_kind` = 'team' AND `management_source_id` <> '')
         OR (`management_source_kind` = 'ownership_transfer' AND `management_source_id` <> '')))
  );

-- === 2. bcs_bots.ownership_version ==========================================
ALTER TABLE `bcs_bots`
  ADD COLUMN `ownership_version` BIGINT NOT NULL DEFAULT 0
    COMMENT 'monotonic ownership version: 0 = uninitialized, 1 = first initialization, +1 per accepted transfer';

-- === 3. bot_ownership_transfers =============================================
CREATE TABLE IF NOT EXISTS `bot_ownership_transfers` (
  `id`                    BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK; auto-increment bigint',
  `gmt_create`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'create time (DB-managed)',
  `gmt_modified`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'update time (DB-managed)',
  `transfer_id`           VARCHAR(64)  NOT NULL COMMENT 'opaque, immutable business transfer id',
  `env`                   VARCHAR(64)  NOT NULL COMMENT 'precise resource env scope',
  `bot_id`                VARCHAR(256) NOT NULL COMMENT 'target physical Bot id',
  `from_user_id`          VARCHAR(256) NOT NULL COMMENT 'initiating owner User ID, immutable after create',
  `to_user_id`            VARCHAR(256) NOT NULL COMMENT 'designated recipient User ID, immutable after create',
  `expected_owner_version` BIGINT      NOT NULL COMMENT 'ownership_version snapshot taken at creation',
  `client_request_id`     VARCHAR(64)  NOT NULL COMMENT 'caller-generated idempotency key',
  `status`                VARCHAR(16)  NOT NULL COMMENT 'pending | accepted | rejected | cancelled | expired | invalidated',
  `expires_at`            TIMESTAMP    NOT NULL COMMENT 'deadline fixed at creation (DB UTC + 7 days)',
  `decision_actor_kind`   VARCHAR(16)  DEFAULT NULL COMMENT 'human | system; NULL while pending',
  `decided_by`            VARCHAR(256) DEFAULT NULL COMMENT 'human terminal: real User ID; system terminal: fixed system marker',
  `decided_at`            TIMESTAMP    NULL DEFAULT NULL COMMENT 'DB-managed decision time; NULL while pending',
  `result_owner_version`  BIGINT       DEFAULT NULL COMMENT 'the committed version of THIS transfer; set only on accepted',
  `terminal_reason`       VARCHAR(32)  DEFAULT NULL COMMENT 'bot_deleted | actor_unavailable | owner_changed; never arbitrary errors',
  `bot_name_snapshot`     VARCHAR(256) DEFAULT NULL COMMENT 'minimal display snapshot for the recipient confirmation',
  `pending_slot`          TINYINT
    GENERATED ALWAYS AS (CASE WHEN `status` = 'pending' THEN 1 ELSE NULL END) STORED
    COMMENT 'nullable generated pending slot (§5.3 MySQL variant)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_transfer_id` (`transfer_id`),
  UNIQUE KEY `uk_bot_transfer_client_request` (`env`, `bot_id`, `from_user_id`, `client_request_id`),
  UNIQUE KEY `uk_bot_transfer_pending_slot` (`env`, `bot_id`, `pending_slot`),
  KEY `idx_transfer_received` (`env`, `to_user_id`, `status`, `gmt_create`, `transfer_id`),
  KEY `idx_transfer_sent` (`env`, `from_user_id`, `status`, `gmt_create`, `transfer_id`),
  CONSTRAINT `chk_transfer_status` CHECK (`status` IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired', 'invalidated')),
  CONSTRAINT `chk_transfer_terminal_reason` CHECK (`terminal_reason` IS NULL
    OR `terminal_reason` IN ('bot_deleted', 'actor_unavailable', 'owner_changed')),
  CONSTRAINT `chk_transfer_decision_kind` CHECK (`decision_actor_kind` IS NULL
    OR `decision_actor_kind` IN ('human', 'system')),
  CONSTRAINT `chk_transfer_decision` CHECK (
    (`status` = 'pending' AND `decision_actor_kind` IS NULL AND `decided_by` IS NULL
       AND `decided_at` IS NULL AND `result_owner_version` IS NULL)
    OR (`status` <> 'pending' AND `decision_actor_kind` IS NOT NULL AND `decided_by` IS NOT NULL
       AND `decided_at` IS NOT NULL)
  ),
  CONSTRAINT `chk_transfer_result_version` CHECK (`result_owner_version` IS NULL OR `status` = 'accepted')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- === 4. bot_manager_changes =================================================
CREATE TABLE IF NOT EXISTS `bot_manager_changes` (
  `id`                    BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK; auto-increment bigint',
  `gmt_create`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'create time (DB-managed)',
  `gmt_modified`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'update time (DB-managed)',
  `audit_id`              VARCHAR(512) NOT NULL COMMENT 'service-generated ledger row id: {operation_id}-{edge_id}, budget bcs_domain::AUTHORITY_AUDIT_ID_VARCHAR_WIDTH',
  `env`                   VARCHAR(64)  NOT NULL COMMENT 'precise resource env scope',
  `bot_id`                VARCHAR(256) NOT NULL COMMENT 'target physical Bot id',
  `subject_user_id`       VARCHAR(256) NOT NULL COMMENT 'authorized/revoked Human User ID',
  `edge_id`               BIGINT       NOT NULL COMMENT 'edge_grants.id of the mutated manager edge',
  `management_source_kind` VARCHAR(32) NOT NULL COMMENT 'direct | team | ownership_transfer of the mutated edge',
  `management_source_id`  VARCHAR(64)  NOT NULL COMMENT 'manual | <team_id> | <transfer_id> of the mutated edge',
  `action`                VARCHAR(16)  NOT NULL COMMENT 'grant | revoke',
  `actor_kind`            VARCHAR(16)  NOT NULL COMMENT 'human | service | system; never a mismatched User ID',
  `actor_id`              VARCHAR(256) NOT NULL COMMENT 'authenticated User ID, verified service id, or fixed system marker',
  `operation_id`          VARCHAR(256) NOT NULL COMMENT 'groups all audit rows of one atomic change (budget bcs_domain::AUTHORITY_OPERATION_ID_VARCHAR_WIDTH)',
  `idempotency_key`       VARCHAR(64)  DEFAULT NULL COMMENT 'required for team sync rows; NULL for non-team operations',
  `decided_at`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'decision time (DB-managed)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_manager_changes_audit_id` (`audit_id`),
  KEY `idx_manager_changes_bot_time` (`env`, `bot_id`, `gmt_create`),
  KEY `idx_manager_changes_operation` (`env`, `operation_id`),
  CONSTRAINT `chk_manager_changes_action` CHECK (`action` IN ('grant', 'revoke')),
  CONSTRAINT `chk_manager_changes_actor_kind` CHECK (`actor_kind` IN ('human', 'service', 'system'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- === 5. bcs_bot_action_audits ===============================================
CREATE TABLE IF NOT EXISTS `bcs_bot_action_audits` (
  `id`                    BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK; auto-increment bigint',
  `gmt_create`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'create time (DB-managed)',
  `gmt_modified`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'update time (DB-managed)',
  `audit_id`              VARCHAR(512) NOT NULL COMMENT 'service-generated record id: {lane}:{env}:{operation_id}:{step_key}, budget bcs_domain::AUTHORITY_AUDIT_ID_VARCHAR_WIDTH',
  `env`                   VARCHAR(64)  NOT NULL COMMENT 'assembly environment',
  `operation_id`          VARCHAR(256) NOT NULL COMMENT 'stable id grouping one operation (budget bcs_domain::AUTHORITY_OPERATION_ID_VARCHAR_WIDTH)',
  `step_key`              VARCHAR(96)  NOT NULL COMMENT 'stable in-operation step key (action/resource/phase vocabulary)',
  `operator_kind`         VARCHAR(16)  NOT NULL COMMENT 'human | bot | system',
  `operator_id`           VARCHAR(256) NOT NULL COMMENT 'trusted User ID, verified Bot ID, or fixed system marker',
  `operator_user_id`      VARCHAR(256) DEFAULT NULL COMMENT 'required for human rows; NULL means genuinely no Human (Bot-only/system)',
  `effective_actor_id`    VARCHAR(256) NOT NULL COMMENT 'Actor selected by application authorization (Bot, Human self actor, or system marker)',
  `resource_kind`         VARCHAR(32)  NOT NULL COMMENT 'bot | group | session | session_file | workspace | message | friend | invitation',
  `resource_id`           VARCHAR(512) NOT NULL COMMENT 'precise resource identifier',
  `action`                VARCHAR(32)  NOT NULL COMMENT 'create | update | delete | share | collect | launch | send | abort | invite',
  `phase`                 VARCHAR(16)  NOT NULL COMMENT 'applied | admitted | completed | failed | unknown',
  `reason_code`           VARCHAR(64)  DEFAULT NULL COMMENT 'optional fixed machine reason; never raw errors or credentials',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_action_audit_id` (`audit_id`),
  UNIQUE KEY `uk_bot_action_audit_slot` (`env`, `operation_id`, `step_key`),
  KEY `idx_bot_action_audit_resource` (`env`, `resource_kind`, `resource_id`, `gmt_create`),
  CONSTRAINT `chk_audit_operator_kind` CHECK (`operator_kind` IN ('human', 'bot', 'system')),
  CONSTRAINT `chk_audit_phase` CHECK (`phase` IN ('applied', 'admitted', 'completed', 'failed', 'unknown')),
  CONSTRAINT `chk_audit_operator` CHECK (
    (`operator_kind` = 'human' AND `operator_user_id` IS NOT NULL)
    OR (`operator_kind` IN ('bot', 'system') AND `operator_user_id` IS NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- === 6. operation_id context columns (§12.5 admitted snapshots) ============
ALTER TABLE `bcs_message_deliveries`
  ADD COLUMN `operation_id` VARCHAR(256) DEFAULT NULL
    COMMENT 'operation of the SAME-transaction admitted audit snapshot; NULL for historical rows (never backfilled)';

ALTER TABLE `bcs_chat_runs`
  ADD COLUMN `operation_id` VARCHAR(256) DEFAULT NULL
    COMMENT 'operation of the SAME-transaction admitted audit snapshot; NULL for historical rows (never backfilled)';

-- === 7. bot_manager_sync_operations =========================================
CREATE TABLE IF NOT EXISTS `bot_manager_sync_operations` (
  `id`                    BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK; auto-increment bigint',
  `gmt_create`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'create time (DB-managed)',
  `gmt_modified`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'update time (DB-managed)',
  `env`                   VARCHAR(64)  NOT NULL COMMENT 'precise resource env scope',
  `service_id`            VARCHAR(256) NOT NULL COMMENT 'verified platform service identity (credential MAX_CLAIM_LEN bound; bcs_domain::AUTHORITY_SERVICE_ID_VARCHAR_WIDTH)',
  `bot_id`                VARCHAR(256) NOT NULL COMMENT 'target physical Bot id',
  `team_id`               VARCHAR(64)  NOT NULL COMMENT 'team in the request URL scope',
  `operation`             VARCHAR(16)  NOT NULL COMMENT 'sync | move (business semantics, never derived from idempotency_key)',
  `new_team_id`           VARCHAR(64)  DEFAULT NULL COMMENT 'move target team; NULL for sync',
  `payload`               TEXT         NOT NULL COMMENT 'canonical normalized request payload JSON',
  `idempotency_key`       VARCHAR(64)  NOT NULL COMMENT 'caller idempotency key scoped to env/service/bot/team',
  `operation_id`           VARCHAR(256) NOT NULL COMMENT 'groups all edge writes and audit rows of this sync (budget bcs_domain::AUTHORITY_OPERATION_ID_VARCHAR_WIDTH)',
  `result`                TEXT         NOT NULL COMMENT 'persistent minimal receipt/result, present even for no-difference syncs',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_manager_sync_scope` (`env`, `service_id`, `bot_id`, `team_id`, `idempotency_key`),
  KEY `idx_manager_sync_bot_time` (`env`, `bot_id`, `gmt_create`),
  CONSTRAINT `chk_sync_operation` CHECK (`operation` IN ('sync', 'move')),
  CONSTRAINT `chk_sync_move_target` CHECK (
    (`operation` = 'sync' AND `new_team_id` IS NULL)
    OR (`operation` = 'move' AND `new_team_id` IS NOT NULL AND `new_team_id` <> ''
       AND `new_team_id` <> `team_id`)
  ),
  CONSTRAINT `chk_sync_keys` CHECK (`idempotency_key` <> '' AND `operation_id` <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- === 8. bot_team_manager_sources ============================================
CREATE TABLE IF NOT EXISTS `bot_team_manager_sources` (
  `id`                    BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK; auto-increment bigint',
  `gmt_create`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'create time (DB-managed)',
  `gmt_modified`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'update time (DB-managed)',
  `env`                   VARCHAR(64)  NOT NULL COMMENT 'precise resource env scope',
  `bot_id`                VARCHAR(256) NOT NULL COMMENT 'target physical Bot id',
  `team_id`               VARCHAR(64)  NOT NULL COMMENT 'platform team binding',
  `status`                VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active | stopped (current binding state)',
  `last_operation_id`     VARCHAR(256) DEFAULT NULL COMMENT 'last sync/move operation that touched this binding (budget bcs_domain::AUTHORITY_OPERATION_ID_VARCHAR_WIDTH)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_team_manager_source` (`env`, `bot_id`, `team_id`),
  KEY `idx_team_manager_source_bot` (`env`, `bot_id`, `status`),
  CONSTRAINT `chk_team_manager_source_status` CHECK (`status` IN ('active', 'stopped'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- === 9. bot_ownership_initializations =======================================
CREATE TABLE IF NOT EXISTS `bot_ownership_initializations` (
  `id`                    BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK; auto-increment bigint',
  `gmt_create`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'create time (DB-managed)',
  `gmt_modified`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'update time (DB-managed)',
  `audit_id`              VARCHAR(512) NOT NULL COMMENT 'ledger row id: {operation_id}-init-{bot_id}, budget bcs_domain::AUTHORITY_AUDIT_ID_VARCHAR_WIDTH',
  `env`                   VARCHAR(64)  NOT NULL COMMENT 'precise resource env scope',
  `bot_id`                VARCHAR(256) NOT NULL COMMENT 'initialized physical Bot id',
  `owner_user_id`         VARCHAR(256) NOT NULL COMMENT 'trusted first owner User ID',
  `initial_version`       BIGINT       NOT NULL DEFAULT 1 COMMENT 'ownership_version committed by the initialization',
  `source`                VARCHAR(32)  NOT NULL COMMENT 'registration | governed_repair (provenance of the initialization)',
  `actor_kind`            VARCHAR(16)  NOT NULL COMMENT 'human | system (registration identity or governed migration)',
  `actor_id`              VARCHAR(256) NOT NULL COMMENT 'trusted registering User ID or fixed system marker',
  `operation_id`          VARCHAR(256) NOT NULL COMMENT 'groups the initialization audit row (budget bcs_domain::AUTHORITY_OPERATION_ID_VARCHAR_WIDTH)',
  `batch_id`              VARCHAR(64)  DEFAULT NULL COMMENT 'governed migration batch; NULL for individual registrations',
  `decided_at`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'initialization time (DB-managed)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ownership_init_audit_id` (`audit_id`),
  KEY `idx_ownership_init_bot` (`env`, `bot_id`, `gmt_create`),
  KEY `idx_ownership_init_operation` (`env`, `operation_id`),
  CONSTRAINT `chk_init_actor_kind` CHECK (`actor_kind` IN ('human', 'system')),
  CONSTRAINT `chk_init_source` CHECK (`source` IN ('registration', 'governed_repair')),
  CONSTRAINT `chk_init_version` CHECK (`initial_version` >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;