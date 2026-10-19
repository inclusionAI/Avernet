CREATE TABLE IF NOT EXISTS bcs_group_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL,
    name TEXT NOT NULL,
    scope_level TEXT NOT NULL CHECK (scope_level IN ('group', 'session', 'round')),
    scope TEXT NOT NULL,
    content TEXT NOT NULL,
    creator TEXT NOT NULL,
    visible_to_json TEXT NOT NULL,
    collect_from_json TEXT NOT NULL,
    supersedes INTEGER NULL,
    change_reason TEXT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NULL,
    is_superseded INTEGER NOT NULL DEFAULT 0 CHECK (is_superseded IN (0, 1)),
    gmt_superseded TEXT NULL,
    tx_time TEXT NOT NULL,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_group_context_active_name_scope
    ON bcs_group_contexts(name, scope)
    WHERE is_superseded = 0;

CREATE INDEX IF NOT EXISTS idx_group_context_name_scope_active
    ON bcs_group_contexts(name, scope, is_superseded)
    WHERE is_superseded = 0;

CREATE INDEX IF NOT EXISTS idx_group_context_scope_active
    ON bcs_group_contexts(scope, is_superseded)
    WHERE is_superseded = 0;

CREATE INDEX IF NOT EXISTS idx_group_context_context_id
    ON bcs_group_contexts(context_id, id);
