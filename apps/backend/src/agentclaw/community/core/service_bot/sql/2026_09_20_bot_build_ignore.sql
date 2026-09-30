-- Apply before deploying build-ignore consumers. Existing runtime rules are unchanged.
CREATE TABLE IF NOT EXISTS ac_bot_build_ignore (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    config_key VARCHAR(64) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) NOT NULL,
    entity_id VARCHAR(1024) NOT NULL,
    bot_id VARCHAR(256) NOT NULL,
    engine_type VARCHAR(64) NOT NULL,
    paths JSON NOT NULL,
    revision INT NOT NULL,
    modifier VARCHAR(1024) NOT NULL,
    gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_build_ignore_tenant_key (avernet_tenant, config_key)
);
