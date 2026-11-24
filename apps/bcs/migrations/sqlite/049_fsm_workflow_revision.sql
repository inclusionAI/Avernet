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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_key_hash TEXT NOT NULL,
    load_key_hash TEXT NOT NULL,
    scope_tenant TEXT NOT NULL,
    scope_principal TEXT NOT NULL,
    group_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    content_format TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    bindings_json TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    compiled_format TEXT NOT NULL,
    compiled_json TEXT NOT NULL,
    published_at INTEGER NOT NULL,
    UNIQUE (revision_key_hash)
);
CREATE INDEX IF NOT EXISTS idx_fsm_workflow_revision_load
    ON bcs_fsm_workflow_revisions (load_key_hash);
-- The group's current publish pointer. It is updated in the same DB
-- transaction as the object INSERT, after it, so a reader never observes a
-- pointer whose object is missing or unverifiable. `revision_counter` is the
-- last revision number assigned for the (scope, group); the next publication
-- takes counter + 1, which makes revision identity store-assigned and
-- monotonic in publication order.
CREATE TABLE IF NOT EXISTS bcs_fsm_group_publications (
    pointer_key_hash TEXT NOT NULL PRIMARY KEY,
    scope_tenant TEXT NOT NULL,
    scope_principal TEXT NOT NULL,
    group_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    current_revision TEXT NOT NULL,
    current_digest TEXT NOT NULL,
    revision_counter INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
