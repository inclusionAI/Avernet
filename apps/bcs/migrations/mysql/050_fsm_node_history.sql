-- Node-isolated interaction history for frozen FSM group workflows (plan T3).
-- Rows are append-only and addressed by one logical node's own identity:
-- scope, group, Sid, frozen workflow and node. There is no Bot dimension, so
-- nodes bound to the same Bot keep separate histories. `id` is the global
-- append order and doubles as the per-node order; history cuts are opaque
-- boundaries over it. Hash keys bind index width; the full node identity is
-- verified on every read for collision safety.
CREATE TABLE IF NOT EXISTS bcs_fsm_node_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    node_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_tenant VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    scope_principal VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    sid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    workflow_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    workflow_revision VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    workflow_digest VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    node_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    interaction_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    entry_json LONGTEXT NOT NULL,
    appended_at BIGINT NOT NULL,
    UNIQUE KEY uk_fsm_node_history_identity (node_key_hash, interaction_id),
    KEY idx_fsm_node_history_scan (node_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
-- Immutable input pins: the exact (round, node) dispatch input materialized
-- from a cut-bounded history (entries' content references, ordered digest,
-- truncation flag). After a freeze this is the only dispatch input source;
-- `state` is 'pinned' until the owning round is terminal with no replayable
-- dispatch left, then 'released' — released pins never fall back to raw reads.
CREATE TABLE IF NOT EXISTS bcs_fsm_node_input_pins (
    pin_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_tenant VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    scope_principal VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    sid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    tid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    round_generation BIGINT UNSIGNED NOT NULL,
    node_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    cut_json LONGTEXT NOT NULL,
    entries_json LONGTEXT NOT NULL,
    history_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    truncated TINYINT NOT NULL DEFAULT 0,
    budget_rows BIGINT UNSIGNED NOT NULL,
    state VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT 'pinned',
    pinned_at BIGINT NOT NULL,
    PRIMARY KEY (pin_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
