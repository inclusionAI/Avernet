CREATE TABLE IF NOT EXISTS bcs_collaboration_drafts (
    draft_id VARCHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner_bot_id VARCHAR(128) NOT NULL,
    head_revision BIGINT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    deleted INT NOT NULL DEFAULT 0,
    PRIMARY KEY (draft_id)
);
CREATE TABLE IF NOT EXISTS bcs_collaboration_draft_revisions (
    draft_id VARCHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision BIGINT NOT NULL,
    payload_json MEDIUMTEXT CHARACTER SET utf8mb4 NOT NULL,
    revoked INT NOT NULL DEFAULT 0,
    PRIMARY KEY (draft_id, revision)
);
