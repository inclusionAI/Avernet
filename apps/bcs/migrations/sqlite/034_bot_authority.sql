-- 033_bot_authority.sql — bot owner/manager authority schema for SQLite
-- (spec: docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md
-- §5 source encoding, §5.2 transfer fields, §5.3 uniqueness/indexes with the
-- SQLite partial-index variants, §5.4 manager audit, §12.5 ordinary
-- business action audit, §13.3 ownership initialization).
--
-- The chain must reach version 32 exactly once and stay frozen afterwards:
-- no later migration may backfill columns introduced here.
--
-- Statement classes in this file, ordered so every statement is directly
-- executable and idempotent (CREATE ... IF NOT EXISTS only, no semicolons
-- inside strings or comments):
--   * the CANONICAL new-shape `edge_grants` TABLE definition. On a fresh
--     database migration 13 already created `edge_grants`, so this CREATE is
--     a no-op and the Rust migration body (src/migrations.rs, version 32)
--     extracts THIS statement to rebuild the existing table under the
--     `edge_grants__authority_rebuild` shadow name, copying every row and
--     preserving the original IDs. The kind/source CHECK inside this DDL is
--     the single textual definition of the role shape — the test
--     sql_files_share_the_domain_source_encoding_constants pins this text to
--     the shared bcs_domain constants.
--   * the replacement unique key (source columns included, runtime-ref
--     dimension preserved because roles fix grant_ref_id = 0), the
--     approved-owner partial unique slot, and the general scan indexes that
--     the rebuild drops with the old table.
--   * one CREATE TABLE + indexes for each new authority object.
-- The NOT NULL column additions for existing tables (bcs_bots.
-- ownership_version, bcs_message_deliveries.operation_id, bcs_chat_runs.
-- operation_id, the edge_grants source columns themselves) are applied by
-- guarded ensure-steps in the Rust migration body, because SQLite has no
-- idempotent ALTER TABLE ADD COLUMN syntax.

-- === edge_grants (canonical new shape, rebuilt only on legacy tables) ======
CREATE TABLE IF NOT EXISTS edge_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    grant_kind TEXT NOT NULL,
    grant_ref_id INTEGER NOT NULL,
    management_source_kind TEXT NOT NULL DEFAULT 'none',
    management_source_id TEXT NOT NULL DEFAULT 'none',
    rules TEXT,
    status TEXT NOT NULL DEFAULT 'approved',
    originator_policy_type TEXT NOT NULL DEFAULT 'any',
    originator_policy_data TEXT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_edge_role_source CHECK (
        (grant_kind IN ('permission_profile', 'rules')
            AND management_source_kind = 'none' AND management_source_id = 'none')
        OR (grant_kind = 'owner' AND grant_ref_id = 0 AND rules IS NULL
            AND management_source_kind = 'owner' AND management_source_id = 'owner')
        OR (grant_kind = 'manager' AND grant_ref_id = 0 AND rules IS NULL
            AND ((management_source_kind = 'direct' AND management_source_id = 'manual')
              OR (management_source_kind = 'team' AND management_source_id <> '')
              OR (management_source_kind = 'ownership_transfer' AND management_source_id <> '')))
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_from_to_env_kind_ref_source ON edge_grants(from_id, to_id, env, grant_kind, grant_ref_id, management_source_kind, management_source_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_active_owner_slot ON edge_grants(env, to_id) WHERE grant_kind = 'owner' AND status = 'approved';
CREATE INDEX IF NOT EXISTS idx_edge_from_env_status ON edge_grants(from_id, env, status);
CREATE INDEX IF NOT EXISTS idx_edge_to_env_status ON edge_grants(to_id, env, status);

-- === bot_ownership_transfers ================================================
CREATE TABLE IF NOT EXISTS bot_ownership_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    transfer_id TEXT NOT NULL,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    from_user_id TEXT NOT NULL,
    to_user_id TEXT NOT NULL,
    expected_owner_version INTEGER NOT NULL,
    client_request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decision_actor_kind TEXT,
    decided_by TEXT,
    decided_at TEXT,
    result_owner_version INTEGER,
    terminal_reason TEXT,
    bot_name_snapshot TEXT,
    CONSTRAINT uk_bot_transfer_id UNIQUE (transfer_id),
    CONSTRAINT chk_transfer_status CHECK (status IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired', 'invalidated')),
    CONSTRAINT chk_transfer_terminal_reason CHECK (terminal_reason IS NULL OR terminal_reason IN ('bot_deleted', 'actor_unavailable', 'owner_changed')),
    CONSTRAINT chk_transfer_decision_kind CHECK (decision_actor_kind IS NULL OR decision_actor_kind IN ('human', 'system')),
    CONSTRAINT chk_transfer_decision CHECK (
        (status = 'pending' AND decision_actor_kind IS NULL AND decided_by IS NULL AND decided_at IS NULL AND result_owner_version IS NULL)
        OR (status <> 'pending' AND decision_actor_kind IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)
    ),
    CONSTRAINT chk_transfer_result_version CHECK (result_owner_version IS NULL OR status = 'accepted')
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_bot_transfer_client_request ON bot_ownership_transfers(env, bot_id, from_user_id, client_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_bot_transfer_pending_slot ON bot_ownership_transfers(env, bot_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_transfer_received ON bot_ownership_transfers(env, to_user_id, status, gmt_create, transfer_id);
CREATE INDEX IF NOT EXISTS idx_transfer_sent ON bot_ownership_transfers(env, from_user_id, status, gmt_create, transfer_id);

-- === bot_manager_changes ====================================================
-- Width budgets mirror the MySQL twin (032): audit_id VARCHAR(512),
-- operation_id VARCHAR(256). Client-supplied ids (team_id, idempotency_key)
-- are length-validated by the application at 64 — SQLite accepts TEXT
-- rows of any length, the MySQL widths are the durable contract.
CREATE TABLE IF NOT EXISTS bot_manager_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    audit_id TEXT NOT NULL,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    subject_user_id TEXT NOT NULL,
    edge_id INTEGER NOT NULL,
    management_source_kind TEXT NOT NULL,
    management_source_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    idempotency_key TEXT,
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_manager_changes_audit_id UNIQUE (audit_id),
    CONSTRAINT chk_manager_changes_action CHECK (action IN ('grant', 'revoke')),
    CONSTRAINT chk_manager_changes_actor_kind CHECK (actor_kind IN ('human', 'service', 'system'))
);
CREATE INDEX IF NOT EXISTS idx_manager_changes_bot_time ON bot_manager_changes(env, bot_id, gmt_create);
CREATE INDEX IF NOT EXISTS idx_manager_changes_operation ON bot_manager_changes(env, operation_id);

-- === bcs_bot_action_audits ===================================================
-- Width budgets mirror the MySQL twin (032): audit_id VARCHAR(512),
-- operation_id VARCHAR(256) — the writer conformance tests assert every
-- composed audit id fits the 512 budget.
CREATE TABLE IF NOT EXISTS bcs_bot_action_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    audit_id TEXT NOT NULL,
    env TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    operator_kind TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    operator_user_id TEXT,
    effective_actor_id TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    action TEXT NOT NULL,
    phase TEXT NOT NULL,
    reason_code TEXT,
    CONSTRAINT uk_bot_action_audit_id UNIQUE (audit_id),
    CONSTRAINT chk_audit_operator_kind CHECK (operator_kind IN ('human', 'bot', 'system')),
    CONSTRAINT chk_audit_phase CHECK (phase IN ('applied', 'admitted', 'completed', 'failed', 'unknown')),
    CONSTRAINT chk_audit_operator CHECK (
        (operator_kind = 'human' AND operator_user_id IS NOT NULL)
        OR (operator_kind IN ('bot', 'system') AND operator_user_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_bot_action_audit_slot ON bcs_bot_action_audits(env, operation_id, step_key);
CREATE INDEX IF NOT EXISTS idx_bot_action_audit_resource ON bcs_bot_action_audits(env, resource_kind, resource_id, gmt_create);

-- === bot_manager_sync_operations ============================================
-- Width budgets mirror the MySQL twin (032): service_id VARCHAR(256)
-- (credential MAX_CLAIM_LEN), operation_id VARCHAR(256), client-supplied
-- team_id / new_team_id / idempotency_key length-validated at 64.
CREATE TABLE IF NOT EXISTS bot_manager_sync_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    env TEXT NOT NULL,
    service_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    new_team_id TEXT,
    payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    result TEXT NOT NULL,
    CONSTRAINT chk_sync_operation CHECK (operation IN ('sync', 'move')),
    CONSTRAINT chk_sync_move_target CHECK (
        (operation = 'sync' AND new_team_id IS NULL)
        OR (operation = 'move' AND new_team_id IS NOT NULL AND new_team_id <> '' AND new_team_id <> team_id)
    ),
    CONSTRAINT chk_sync_keys CHECK (idempotency_key <> '' AND operation_id <> '')
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_manager_sync_scope ON bot_manager_sync_operations(env, service_id, bot_id, team_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_manager_sync_bot_time ON bot_manager_sync_operations(env, bot_id, gmt_create);

-- === bot_team_manager_sources ===============================================
-- last_operation_id mirrors the MySQL twin VARCHAR(256). team_id mirrored
-- at VARCHAR(64) (application-validated).
CREATE TABLE IF NOT EXISTS bot_team_manager_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_operation_id TEXT,
    CONSTRAINT uk_team_manager_source UNIQUE (env, bot_id, team_id),
    CONSTRAINT chk_team_manager_source_status CHECK (status IN ('active', 'stopped'))
);
CREATE INDEX IF NOT EXISTS idx_team_manager_source_bot ON bot_team_manager_sources(env, bot_id, status);

-- === bot_ownership_initializations ==========================================
-- Width budgets mirror the MySQL twin (032): audit_id VARCHAR(512),
-- operation_id VARCHAR(256), batch_id VARCHAR(64) (application-validated).
CREATE TABLE IF NOT EXISTS bot_ownership_initializations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    audit_id TEXT NOT NULL,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    initial_version INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    batch_id TEXT,
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_ownership_init_audit_id UNIQUE (audit_id),
    CONSTRAINT chk_init_actor_kind CHECK (actor_kind IN ('human', 'system')),
    CONSTRAINT chk_init_source CHECK (source IN ('registration', 'governed_repair')),
    CONSTRAINT chk_init_version CHECK (initial_version >= 1)
);
CREATE INDEX IF NOT EXISTS idx_ownership_init_bot ON bot_ownership_initializations(env, bot_id, gmt_create);
CREATE INDEX IF NOT EXISTS idx_ownership_init_operation ON bot_ownership_initializations(env, operation_id);