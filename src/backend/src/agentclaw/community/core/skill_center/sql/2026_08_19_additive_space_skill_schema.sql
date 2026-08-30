-- F01 / compatibility baseline A: additive Space Skill persistence only.
--
-- Run the paired verify script before and after this file.  It intentionally
-- does not delete duplicate ac_skill_set_skill rows: production rollout must
-- provide the reviewed three primary keys from the ODC preflight (§15.1) before
-- the unique key below is added.  This prevents a broad, irreversible cleanup.
--
-- The IF NOT EXISTS clauses make re-running this additive stage safe after an
-- interrupted deployment.  They require the OceanBase MySQL-compatible DDL
-- profile used by this service; run on a staging clone before production.

ALTER TABLE ac_skill
  MODIFY COLUMN env VARCHAR(20) NOT NULL,
  ADD COLUMN IF NOT EXISTS draft_target_version INT NULL COMMENT '活动草稿目标 Version Ordinal',
  ADD COLUMN IF NOT EXISTS draft_status VARCHAR(16) NULL COMMENT 'NULL/EDITING/FROZEN',
  ADD COLUMN IF NOT EXISTS draft_description TEXT NULL COMMENT '当前 Draft 的 SKILL.md description',
  ADD COLUMN IF NOT EXISTS draft_source_kind VARCHAR(32) NULL COMMENT 'FOLDER/GIT/PUBLISHED_VERSION',
  ADD COLUMN IF NOT EXISTS creation_request_id VARCHAR(128) NULL COMMENT '创建幂等请求身份',
  ADD COLUMN IF NOT EXISTS creation_request_hash VARCHAR(64) NULL COMMENT '创建命令指纹',
  ADD COLUMN IF NOT EXISTS draft_request_id VARCHAR(128) NULL COMMENT '当前升级 Draft 幂等请求身份',
  ADD COLUMN IF NOT EXISTS offline_at TIMESTAMP NULL COMMENT 'TeamClaw 本地可恢复下线时间',
  ADD COLUMN IF NOT EXISTS offline_by VARCHAR(128) NULL COMMENT 'TeamClaw 本地可恢复下线操作者',
  ADD COLUMN IF NOT EXISTS source_repo_url VARCHAR(2048) NULL COMMENT 'Git 导入仓库 URL',
  ADD COLUMN IF NOT EXISTS source_branch VARCHAR(512) NULL COMMENT '首次导入解析的固定分支',
  ADD COLUMN IF NOT EXISTS source_subdir VARCHAR(1024) NULL COMMENT '仓库内 Skill 子目录',
  ADD COLUMN IF NOT EXISTS source_commit_sha VARCHAR(64) NULL COMMENT '最近一次成功 Source Snapshot commit';

CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_uuid
  ON ac_skill (avernet_tenant, env, skill_uuid);
CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_creation_request
  ON ac_skill (avernet_tenant, env, creation_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_draft_request
  ON ac_skill (avernet_tenant, env, draft_request_id);

-- ac_space and ac_space_member are owned by the unified Space migration:
-- core/spaces/sql/2026_08_17_spaces.sql. Apply that file before this F01 DDL.

CREATE TABLE IF NOT EXISTS ac_skill_space_binding (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id BIGINT UNSIGNED NOT NULL,
  space_id BIGINT UNSIGNED NOT NULL,
  created_by VARCHAR(128) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_skill_ownership (avernet_tenant, env, skill_id),
  KEY idx_space_skills (avernet_tenant, env, space_id),
  CONSTRAINT ck_skill_ownership_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_skill_grant (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id BIGINT UNSIGNED NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  role VARCHAR(16) NOT NULL COMMENT 'OWNER/MANAGER',
  status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/REVOKED',
  owner_slot TINYINT NULL COMMENT 'ACTIVE OWNER 固定为 1，其余 NULL',
  granted_by VARCHAR(128) NOT NULL,
  grant_reason VARCHAR(1024) NULL COMMENT '授权或 Owner 转移的审计原因',
  revoked_at TIMESTAMP NULL,
  revoked_by VARCHAR(128) NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_skill_grant_user (avernet_tenant, env, skill_id, user_id),
  UNIQUE KEY uk_skill_active_owner (avernet_tenant, env, skill_id, owner_slot),
  KEY idx_skill_grant_user (avernet_tenant, env, user_id, status),
  CONSTRAINT ck_skill_grant_role CHECK (role IN ('OWNER', 'MANAGER')),
  CONSTRAINT ck_skill_grant_status CHECK (status IN ('ACTIVE', 'REVOKED')),
  CONSTRAINT ck_skill_active_owner_slot CHECK (
    (role = 'OWNER' AND status = 'ACTIVE' AND owner_slot IS NOT NULL AND owner_slot = 1)
    OR ((role <> 'OWNER' OR status <> 'ACTIVE') AND owner_slot IS NULL)
  ),
  CONSTRAINT ck_skill_grant_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_skill_draft_edit_lease (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id BIGINT UNSIGNED NOT NULL,
  holder_user_id VARCHAR(128) NULL,
  fencing_token BIGINT UNSIGNED NOT NULL DEFAULT 0,
  acquired_at TIMESTAMP NULL,
  last_takeover_by VARCHAR(128) NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_skill_edit_lease (avernet_tenant, env, skill_id),
  CONSTRAINT ck_skill_edit_lease_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_skill_version (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id BIGINT UNSIGNED NOT NULL,
  publication_attempt_id BIGINT UNSIGNED NULL,
  version_ordinal INT UNSIGNED NOT NULL,
  status VARCHAR(24) NOT NULL COMMENT 'MATERIALIZING/PUBLISHED',
  sc_version_number VARCHAR(128) NOT NULL,
  sc_skill_id BIGINT NULL,
  sc_version_id BIGINT NULL,
  sc_sha256 VARCHAR(128) NULL,
  name VARCHAR(256) NOT NULL,
  description TEXT NULL,
  metadata_json MEDIUMTEXT NULL,
  published_at TIMESTAMP NULL,
  created_by VARCHAR(128) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_skill_version_ordinal (avernet_tenant, env, skill_id, version_ordinal),
  UNIQUE KEY uk_skill_sc_version (avernet_tenant, env, skill_id, sc_version_number),
  UNIQUE KEY uk_version_attempt (avernet_tenant, env, publication_attempt_id),
  KEY idx_skill_latest (avernet_tenant, env, skill_id, status, version_ordinal),
  CONSTRAINT ck_skill_version_status CHECK (status IN ('MATERIALIZING', 'PUBLISHED')),
  CONSTRAINT ck_skill_version_ordinal CHECK (version_ordinal >= 1),
  CONSTRAINT ck_skill_version_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_skill_publication_attempt (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id BIGINT UNSIGNED NOT NULL,
  request_id VARCHAR(128) NOT NULL,
  active_skill_key VARCHAR(256) NULL,
  target_version_ordinal INT UNSIGNED NOT NULL,
  sc_version_number VARCHAR(128) NULL,
  skill_version_id BIGINT UNSIGNED NULL,
  status VARCHAR(32) NOT NULL,
  error_code VARCHAR(128) NULL,
  error_message TEXT NULL,
  recovery_state VARCHAR(24) NULL,
  recovery_kind VARCHAR(24) NULL,
  sc_post_started_at TIMESTAMP NULL,
  sc_accepted_at TIMESTAMP NULL,
  completed_at TIMESTAMP NULL,
  created_by VARCHAR(128) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_publish_request (avernet_tenant, env, skill_id, request_id),
  UNIQUE KEY uk_active_skill_publish (active_skill_key),
  KEY idx_publish_skill_history (avernet_tenant, env, skill_id, gmt_created),
  CONSTRAINT ck_skill_publication_attempt_status CHECK (status IN ('PREPARING', 'SC_SUBMITTING', 'WAITING_SC', 'RESULT_UNKNOWN', 'MATERIALIZING', 'SUCCEEDED', 'FAILED')),
  CONSTRAINT ck_skill_publication_recovery_state CHECK (recovery_state IS NULL OR recovery_state IN ('AUTO_RETRYING', 'AVAILABLE', 'NOT_AVAILABLE')),
  CONSTRAINT ck_skill_publication_recovery_kind CHECK (recovery_kind IS NULL OR recovery_kind IN ('PREPARATION', 'SC_STATUS_CHECK', 'MATERIALIZATION')),
  CONSTRAINT ck_attempt_target_ordinal CHECK (target_version_ordinal >= 1),
  CONSTRAINT ck_skill_publication_attempt_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE ac_skill_set_skill
  MODIFY COLUMN env VARCHAR(20) NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_set_skill
  ON ac_skill_set_skill (avernet_tenant, env, skill_set_id, skill_id);

ALTER TABLE ac_skill_center_sync_log
  ADD COLUMN IF NOT EXISTS avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  ADD COLUMN IF NOT EXISTS skill_version_id BIGINT UNSIGNED NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uk_center_version_materialization
  ON ac_skill_center_sync_log (avernet_tenant, env, skill_version_id);
