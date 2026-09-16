-- Deploy before enabling bot_storage/upfs_rollout. No rollout is enabled here.
-- A Bot may have different config keys within the same entity/environment.
-- Each key has one current row; storage_policy is not a history/event table.
CREATE TABLE IF NOT EXISTS ac_bot_common_config (
    id BIGINT NOT NULL AUTO_INCREMENT,
    bot_id VARCHAR(128) NOT NULL,
    entity_id VARCHAR(128) NOT NULL,
    env VARCHAR(32) NOT NULL,
    config_key VARCHAR(64) NOT NULL,
    config_value TEXT NOT NULL COMMENT 'JSON configuration',
    is_delete TINYINT NOT NULL DEFAULT 0,
    gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_bot_config (bot_id, entity_id, env, config_key)
);
