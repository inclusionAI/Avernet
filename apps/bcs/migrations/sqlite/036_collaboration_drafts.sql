CREATE TABLE IF NOT EXISTS bcs_collaboration_drafts (
    draft_id TEXT NOT NULL,
    owner_bot_id TEXT NOT NULL,
    head_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (draft_id)
);
CREATE TABLE IF NOT EXISTS bcs_collaboration_draft_revisions (
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (draft_id, revision)
);
