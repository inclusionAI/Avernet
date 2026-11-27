-- Node-isolated interaction history for frozen FSM group workflows (plan T3).
-- Rows are append-only and addressed by one logical node's own identity:
-- scope, group, Sid, frozen workflow and node. There is no Bot dimension, so
-- nodes bound to the same Bot keep separate histories. `id` is the global
-- append order and doubles as the per-node order; history cuts are opaque
-- boundaries over it. Hash keys bind index width; the full node identity is
-- verified on every read for collision safety.
CREATE TABLE IF NOT EXISTS bcs_fsm_node_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_key_hash TEXT NOT NULL,
    scope_tenant TEXT NOT NULL,
    scope_principal TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sid TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_revision TEXT NOT NULL,
    workflow_digest TEXT NOT NULL,
    node_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    appended_at INTEGER NOT NULL,
    UNIQUE (node_key_hash, interaction_id)
);
CREATE INDEX IF NOT EXISTS idx_fsm_node_history_scan
    ON bcs_fsm_node_history (node_key_hash, id);
-- Immutable input pins: the exact (round, node) dispatch input materialized
-- from a cut-bounded history (entries' content references, ordered digest,
-- truncation flag). After a freeze this is the only dispatch input source;
-- `state` is 'pinned' until the owning round is terminal with no replayable
-- dispatch left, then 'released' — released pins never fall back to raw reads.
CREATE TABLE IF NOT EXISTS bcs_fsm_node_input_pins (
    pin_key_hash TEXT NOT NULL PRIMARY KEY,
    scope_tenant TEXT NOT NULL,
    scope_principal TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sid TEXT NOT NULL,
    tid TEXT NOT NULL,
    round_generation INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    cut_json TEXT NOT NULL,
    entries_json TEXT NOT NULL,
    history_digest TEXT NOT NULL,
    truncated INTEGER NOT NULL,
    budget_rows INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pinned',
    pinned_at INTEGER NOT NULL
);
