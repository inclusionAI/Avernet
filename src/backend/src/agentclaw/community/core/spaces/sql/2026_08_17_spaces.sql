-- Space and member foundation for the Skill Space refactor.
CREATE TABLE IF NOT EXISTS ac_space (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    space_code VARCHAR(64) NOT NULL,
    space_type VARCHAR(32) NOT NULL COMMENT 'PERSONAL | TEAM',
    name VARCHAR(128) NOT NULL,
    personal_owner_id VARCHAR(256) NULL,
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    created_by VARCHAR(256) NOT NULL,
    updated_by VARCHAR(256) NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_space_code_env (avernet_tenant, space_code, env),
    UNIQUE KEY uk_space_personal_owner_env (avernet_tenant, personal_owner_id, env),
    KEY idx_space_type_env (avernet_tenant, space_type, env)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_space_member (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    space_id BIGINT UNSIGNED NOT NULL,
    user_id VARCHAR(256) NOT NULL,
    role VARCHAR(32) NOT NULL COMMENT 'OWNER | MEMBER',
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    created_by VARCHAR(256) NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_space_member_user_env (avernet_tenant, space_id, user_id, env),
    KEY idx_space_member_user (avernet_tenant, user_id, env),
    KEY idx_space_member_role (avernet_tenant, space_id, role, env)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
