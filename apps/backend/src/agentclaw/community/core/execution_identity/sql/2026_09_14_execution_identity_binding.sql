CREATE TABLE ac_bot_execution_identity_binding (
    id BIGINT NOT NULL AUTO_INCREMENT,
    bot_pk BIGINT NOT NULL COMMENT 'ac_bots.id; stable internal Bot identity',
    execution_workno VARCHAR(1024) NOT NULL,
    identity_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    authorization_id VARCHAR(256) NULL,
    credential_id VARCHAR(256) NULL,
    agent_id VARCHAR(1200) NULL,
    credential_status VARCHAR(32) NULL,
    failure_reason VARCHAR(1024) NULL,
    modifier_id VARCHAR(1024) NOT NULL,
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_bot_execution_identity_state (bot_pk, env, status)
) COMMENT='Persistent AgentPass execution identity transition';
