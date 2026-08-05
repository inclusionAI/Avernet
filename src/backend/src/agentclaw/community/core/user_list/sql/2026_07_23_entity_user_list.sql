CREATE TABLE IF NOT EXISTS ac_entity_user_list (
    id BIGINT NOT NULL AUTO_INCREMENT,
    entity_id VARCHAR(1024) NOT NULL,
    user_list_type VARCHAR(64) NOT NULL,
    env VARCHAR(20) NOT NULL,
    gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_entity_user_list_scope (entity_id, user_list_type, env),
    KEY idx_entity_user_list_lookup (env, user_list_type, entity_id)
);
