CREATE TABLE IF NOT EXISTS ac_bot_mcp_config (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(50) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    server_code VARCHAR(256) NOT NULL,
    config TEXT NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_bot_mcp_config
      (avernet_tenant, env, owner_id, bot_id, server_code),
    KEY idx_bot_mcp_config_bot
      (avernet_tenant, env, owner_id, bot_id)
);
