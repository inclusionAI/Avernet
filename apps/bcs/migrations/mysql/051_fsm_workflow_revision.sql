-- Immutable workflow-revision directory for frozen FSM group workflows (plan W).
-- One publication freezes one (scope, group) to a store-assigned immutable
-- revision: the workflow definition, the compiled validation snapshot and the
-- frozen participant-binding manifest are stored as one object row addressed
-- by (scope, group, workflow, revision); the group's current publish pointer
-- is a separate row updated only after that object verifiably exists. Object
-- rows have no update path, so one revision identity may never carry
-- different content. Sessions pin revisions and the directory performs no GC:
-- pinned and active-context revisions stay addressable for recovery, and any
-- future reclamation must prove a revision is unreferenced before deleting it.
CREATE TABLE IF NOT EXISTS bcs_fsm_workflow_revisions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    revision_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    load_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_tenant VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    scope_principal VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    workflow_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    revision VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    content_format VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    content_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    definition_json LONGTEXT NOT NULL,
    bindings_json LONGTEXT NOT NULL,
    limits_json LONGTEXT NOT NULL,
    compiled_format VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    compiled_json LONGTEXT NOT NULL,
    published_at BIGINT NOT NULL,
    UNIQUE KEY uk_fsm_workflow_revision_identity (revision_key_hash),
    KEY idx_fsm_workflow_revision_load (load_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
-- The group's current publish pointer. It is updated in the same DB
-- transaction as the object INSERT, after it, so a reader never observes a
-- pointer whose object is missing or unverifiable. `revision_counter` is the
-- last revision number assigned for the (scope, group); the next publication
-- takes counter + 1, which makes revision identity store-assigned and
-- monotonic in publication order.
CREATE TABLE IF NOT EXISTS bcs_fsm_group_publications (
    pointer_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_tenant VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    scope_principal VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    workflow_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    current_revision VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    current_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision_counter BIGINT UNSIGNED NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (pointer_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
