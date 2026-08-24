-- MCP Direct Installation uses the same owner-qualified Bot identity as Skill.
-- Membership stays in its legacy association table; the canonical service
-- enforces ordinary-Set uniqueness under the Bot mutation lease.
CREATE TABLE IF NOT EXISTS ac_bot_mcp_installation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(50) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    server_code VARCHAR(256) NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_bot_mcp_installation
      (avernet_tenant, env, owner_id, bot_id, server_code),
    KEY idx_bot_mcp_installation_bot
      (avernet_tenant, env, owner_id, bot_id)
);
