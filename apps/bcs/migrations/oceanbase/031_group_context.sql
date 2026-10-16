CREATE TABLE IF NOT EXISTS bcs_group_contexts (
    id BIGINT NOT NULL AUTO_INCREMENT,
    context_id VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    scope_level VARCHAR(16) NOT NULL,
    scope VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    creator VARCHAR(255) NOT NULL,
    visible_to_json JSON NOT NULL,
    collect_from_json JSON NOT NULL,
    supersedes BIGINT NULL,
    change_reason VARCHAR(1024) NULL,
    valid_from DATETIME(3) NOT NULL,
    valid_to DATETIME(3) NULL,
    is_superseded BOOLEAN NOT NULL DEFAULT FALSE,
    gmt_superseded DATETIME(3) NULL,
    tx_time DATETIME(3) NOT NULL,
    gmt_create DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    gmt_modified DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_group_context_name_scope_active (name, scope, is_superseded),
    KEY idx_group_context_scope_active (scope, is_superseded),
    KEY idx_group_context_context_id (context_id, id)
);

CREATE UNIQUE INDEX uk_group_context_active_name_scope
    ON bcs_group_contexts(name, scope)
    FILTER (is_superseded = 0);
