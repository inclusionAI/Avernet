-- Space foundation plus F01 final-schema upgrade.
--
-- Existing installations may already have the original 2026-08-17 tables.
-- Therefore CREATE TABLE is followed by explicit additive ALTER statements:
-- CREATE TABLE IF NOT EXISTS alone is not a migration and must not be relied
-- upon to supply F01 columns. SkillCenter team identifiers are retained as
-- opaque VARCHAR values and must not be coerced to numeric ids.

CREATE TABLE IF NOT EXISTS ac_space (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    space_code VARCHAR(128) NOT NULL,
    space_type VARCHAR(16) NOT NULL COMMENT 'PERSONAL/TEAM',
    name VARCHAR(256) NOT NULL,
    description TEXT NULL,
    personal_owner_id VARCHAR(128) NULL,
    sc_team_id VARCHAR(64) DEFAULT NULL COMMENT 'SkillCenter团队ID，团队空间同步SC成功后写入',
    sc_mapping_status VARCHAR(24) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACTIVE/INACTIVE/CLEANUP_FAILED',
    created_by VARCHAR(128) NOT NULL,
    updated_by VARCHAR(256) NOT NULL,
    deleted_at TIMESTAMP NULL,
    deleted_by VARCHAR(128) NULL,
    env VARCHAR(20) NOT NULL,
    gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_space_code (env, space_code),
    UNIQUE KEY uk_personal_space (env, personal_owner_id),
    UNIQUE KEY uk_sc_team_id_env (sc_team_id, env),
    KEY idx_space_sc_team (env, sc_team_id),
    CONSTRAINT ck_space_type CHECK (space_type IN ('PERSONAL', 'TEAM')),
    CONSTRAINT ck_space_mapping_status CHECK (sc_mapping_status IN ('PENDING', 'ACTIVE', 'INACTIVE', 'CLEANUP_FAILED')),
    CONSTRAINT ck_space_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE ac_space
    MODIFY COLUMN space_code VARCHAR(128) NOT NULL,
    MODIFY COLUMN space_type VARCHAR(16) NOT NULL,
    MODIFY COLUMN name VARCHAR(256) NOT NULL,
    MODIFY COLUMN personal_owner_id VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS description TEXT NULL,
    ADD COLUMN IF NOT EXISTS sc_mapping_status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS updated_by VARCHAR(256) NOT NULL DEFAULT '',
    MODIFY COLUMN env VARCHAR(20) NOT NULL;

ALTER TABLE ac_space MODIFY COLUMN sc_team_id VARCHAR(64) DEFAULT NULL COMMENT 'SkillCenter团队ID，团队空间同步SC成功后写入';
UPDATE ac_space SET sc_mapping_status = 'PENDING' WHERE sc_mapping_status IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uk_space_code
    ON ac_space (env, space_code);
CREATE UNIQUE INDEX IF NOT EXISTS uk_personal_space
    ON ac_space (env, personal_owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_sc_team_id_env
    ON ac_space (sc_team_id, env);
CREATE INDEX IF NOT EXISTS idx_space_sc_team
    ON ac_space (env, sc_team_id);
ALTER TABLE ac_space
    ADD CONSTRAINT IF NOT EXISTS ck_space_type CHECK (space_type IN ('PERSONAL', 'TEAM')),
    ADD CONSTRAINT IF NOT EXISTS ck_space_mapping_status CHECK (sc_mapping_status IN ('PENDING', 'ACTIVE', 'INACTIVE', 'CLEANUP_FAILED')),
    ADD CONSTRAINT IF NOT EXISTS ck_space_env_not_empty CHECK (env <> '');

CREATE TABLE IF NOT EXISTS ac_space_member (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    space_id BIGINT UNSIGNED NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    role VARCHAR(24) NOT NULL COMMENT 'ADMINISTRATOR/MEMBER',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/INACTIVE',
    created_by VARCHAR(128) NOT NULL,
    removed_at TIMESTAMP NULL,
    removed_by VARCHAR(128) NULL,
    env VARCHAR(20) NOT NULL,
    gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_space_member (env, space_id, user_id),
    KEY idx_space_member_user (env, user_id, status),
    CONSTRAINT ck_space_member_role CHECK (role IN ('ADMINISTRATOR', 'MEMBER')),
    CONSTRAINT ck_space_member_status CHECK (status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT ck_space_member_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE ac_space_member
    MODIFY COLUMN user_id VARCHAR(128) NOT NULL,
    MODIFY COLUMN role VARCHAR(24) NOT NULL,
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    ADD COLUMN IF NOT EXISTS removed_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS removed_by VARCHAR(128) NULL,
    MODIFY COLUMN env VARCHAR(20) NOT NULL;
UPDATE ac_space_member SET role = 'ADMINISTRATOR' WHERE role = 'OWNER';
UPDATE ac_space_member SET status = 'ACTIVE' WHERE status IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uk_space_member
    ON ac_space_member (env, space_id, user_id);
CREATE INDEX IF NOT EXISTS idx_space_member_user
    ON ac_space_member (env, user_id, status);
ALTER TABLE ac_space_member
    ADD CONSTRAINT IF NOT EXISTS ck_space_member_role CHECK (role IN ('ADMINISTRATOR', 'MEMBER')),
    ADD CONSTRAINT IF NOT EXISTS ck_space_member_status CHECK (status IN ('ACTIVE', 'INACTIVE')),
    ADD CONSTRAINT IF NOT EXISTS ck_space_member_env_not_empty CHECK (env <> '');
