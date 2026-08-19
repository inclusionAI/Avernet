-- Space-scoped stable references to marketplace resources.
CREATE TABLE IF NOT EXISTS ac_market_favorite (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    space_id BIGINT UNSIGNED NOT NULL,
    user_id VARCHAR(256) NOT NULL,
    target_type VARCHAR(32) NOT NULL COMMENT 'SKILL | MCP',
    target_code VARCHAR(128) NOT NULL,
    created_by VARCHAR(256) NOT NULL,
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_market_favorite_user_target_env (
        avernet_tenant, env, user_id, target_type, target_code
    ),
    KEY idx_market_favorite_user_space (
        avernet_tenant, env, user_id, space_id, gmt_modified
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
