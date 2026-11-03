-- 037_bot_authority.sql — Bot owner/manager authority (spec §4).
-- SQLite counterpart of migrations/mysql/033_bot_authority.sql
-- executed by the bcs SQLite migration runner (the guarded
-- `bcs_bots` ALTER below is applied by the Rust arm only when
-- the column is missing — fresh databases already create it in
-- the baseline DDL).
--
-- SQLite TEXT comparison is BINARY by default, which is what the
-- identity rules require (spec §3.2.6).

-- bcs_bots.ownership_version: 0 = uninitialized (guarded in the
-- runner — fresh DBs get it from the baseline DDL).
ALTER TABLE bcs_bots ADD COLUMN ownership_version INTEGER NOT NULL DEFAULT 0;

-- edge_grants unique key now carries grant_kind: owner/manager
-- edges use grant_ref_id = 0 and must coexist with the runtime
-- profile/rules edges of the same (from, to) pair. The new index is
-- created BEFORE the old one is dropped (SQLite has no one-statement
-- DROP+ADD, and the migration runner applies statements
-- sequentially), so a unique constraint covering the old key's
-- dimension is enforced at every instant: a concurrent old-code
-- writer can never slip a row into an unprotected window — it only
-- ever sees the old (still enforced) key. Owner/manager writes are
-- new-code paths that start after the deploy completes. Existing
-- rows always satisfy the new key because any 5-column duplicate
-- was already forbidden by the old key.
CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_from_to_env_kind_ref
    ON edge_grants (from_id, to_id, env, grant_kind, grant_ref_id);
DROP INDEX IF EXISTS uk_edge_from_to_env_ref;

-- Exactly one approved owner per (env, to_id) — SQLite partial
-- unique index (spec §4.1).
CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_approved_owner
    ON edge_grants (env, to_id)
    WHERE grant_kind = 'owner' AND status = 'approved';

-- Authority read indexes (role/roles_for/managers).
CREATE INDEX IF NOT EXISTS idx_edge_bot_kind_status
    ON edge_grants (env, to_id, grant_kind, status);

-- ============ bot_ownership_transfers ============
-- Reusable current-state row per (env, bot_id, target)
-- one pending per bot via a partial unique index.
CREATE TABLE IF NOT EXISTS bot_ownership_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    target_owner_actor_id TEXT NOT NULL,
    from_owner_actor_id TEXT NOT NULL,
    expected_ownership_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    decided_by_kind TEXT,
    decided_by_id TEXT,
    reason TEXT,
    result_ownership_version INTEGER,
    bot_name_snapshot TEXT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (env, bot_id, target_owner_actor_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_transfer_pending
    ON bot_ownership_transfers (env, bot_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_transfer_received
    ON bot_ownership_transfers (env, target_owner_actor_id, status, requested_at);
CREATE INDEX IF NOT EXISTS idx_transfer_sent
    ON bot_ownership_transfers (env, from_owner_actor_id, status, requested_at);

-- ============ bot_authority_changes ============
-- Permission-scope audit (spec §4.4). Writers chunk VALUES to
-- ≤99 rows per statement (SQLite 999-bind limit, F1) and share
-- one transaction with the data change.
CREATE TABLE IF NOT EXISTS bot_authority_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    mutation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    before_state TEXT,
    after_state TEXT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (env, mutation_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_change_bot
    ON bot_authority_changes (env, bot_id, sequence);
