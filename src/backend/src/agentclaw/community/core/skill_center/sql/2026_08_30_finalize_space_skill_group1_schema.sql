-- Phase 2 Group 1 additive convergence for databases that already applied F01.
--
-- F01 shipped before the final recoverable Offline and Publication Attempt
-- contracts.  These columns and constraints have no production caller, so the
-- convergence is deliberately direct: no long-lived dual fields or states.

ALTER TABLE ac_skill
  ADD COLUMN IF NOT EXISTS draft_description TEXT NULL COMMENT '当前 Draft 的 SKILL.md description',
  ADD COLUMN IF NOT EXISTS draft_source_kind VARCHAR(32) NULL COMMENT 'FOLDER/GIT/PUBLISHED_VERSION',
  ADD COLUMN IF NOT EXISTS creation_request_id VARCHAR(128) NULL COMMENT '创建幂等请求身份',
  ADD COLUMN IF NOT EXISTS creation_request_hash VARCHAR(64) NULL COMMENT '创建命令指纹',
  ADD COLUMN IF NOT EXISTS offline_at TIMESTAMP NULL COMMENT 'TeamClaw 本地可恢复下线时间',
  ADD COLUMN IF NOT EXISTS offline_by VARCHAR(128) NULL COMMENT 'TeamClaw 本地可恢复下线操作者',
  DROP COLUMN IF EXISTS retired_at,
  DROP COLUMN IF EXISTS retired_by;

CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_creation_request
  ON ac_skill (avernet_tenant, env, creation_request_id);

CREATE TABLE IF NOT EXISTS ac_skill_draft_upgrade_request (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id BIGINT UNSIGNED NOT NULL,
  space_id BIGINT UNSIGNED NOT NULL,
  request_id VARCHAR(128) NOT NULL,
  target_version_ordinal INT UNSIGNED NOT NULL,
  status VARCHAR(16) NOT NULL COMMENT 'ACTIVE/SPENT',
  created_by VARCHAR(128) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  env VARCHAR(20) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_skill_upgrade_request (avernet_tenant, env, request_id),
  KEY idx_skill_upgrade_history (avernet_tenant, env, skill_id, gmt_created),
  CONSTRAINT ck_skill_upgrade_request_status CHECK (status IN ('ACTIVE', 'SPENT')),
  CONSTRAINT ck_skill_upgrade_target_ordinal CHECK (target_version_ordinal >= 1),
  CONSTRAINT ck_skill_upgrade_request_env_not_empty CHECK (env <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Preserve any pre-finalization active request before retiring the transient
-- ac_skill field. Final Group 1 deployments have no caller yet, but making the
-- convergence lossless also keeps interrupted staging rollouts recoverable.
SET @backfill_skill_upgrade_requests = IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'ac_skill'
       AND COLUMN_NAME = 'draft_request_id'
  ),
  'INSERT INTO ac_skill_draft_upgrade_request '
  '(skill_id, space_id, request_id, target_version_ordinal, status, created_by, '
  'avernet_tenant, env, gmt_created, gmt_modified) '
  'SELECT skill.id, binding.space_id, skill.draft_request_id, '
  'skill.draft_target_version, ''ACTIVE'', owner_grant.user_id, '
  'skill.avernet_tenant, skill.env, skill.gmt_modified, skill.gmt_modified '
  'FROM ac_skill skill JOIN ac_skill_space_binding binding '
  'ON binding.skill_id = skill.id '
  'AND binding.avernet_tenant = skill.avernet_tenant '
  'AND binding.env = skill.env JOIN ac_skill_grant owner_grant '
  'ON owner_grant.skill_id = skill.id '
  'AND owner_grant.avernet_tenant = skill.avernet_tenant '
  'AND owner_grant.env = skill.env AND owner_grant.role = ''OWNER'' '
  'AND owner_grant.status = ''ACTIVE'' '
  'WHERE skill.draft_request_id IS NOT NULL '
  'ON DUPLICATE KEY UPDATE request_id = '
  'ac_skill_draft_upgrade_request.request_id',
  'SELECT 1'
);
PREPARE backfill_skill_upgrade_requests_stmt FROM @backfill_skill_upgrade_requests;
EXECUTE backfill_skill_upgrade_requests_stmt;
DEALLOCATE PREPARE backfill_skill_upgrade_requests_stmt;

SET @drop_skill_draft_request_index = IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'ac_skill'
       AND INDEX_NAME = 'uk_skill_draft_request'
  ),
  'ALTER TABLE ac_skill DROP INDEX uk_skill_draft_request',
  'SELECT 1'
);
PREPARE drop_skill_draft_request_index_stmt FROM @drop_skill_draft_request_index;
EXECUTE drop_skill_draft_request_index_stmt;
DEALLOCATE PREPARE drop_skill_draft_request_index_stmt;

SET @drop_skill_draft_request_column = IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'ac_skill'
       AND COLUMN_NAME = 'draft_request_id'
  ),
  'ALTER TABLE ac_skill DROP COLUMN draft_request_id',
  'SELECT 1'
);
PREPARE drop_skill_draft_request_column_stmt FROM @drop_skill_draft_request_column;
EXECUTE drop_skill_draft_request_column_stmt;
DEALLOCATE PREPARE drop_skill_draft_request_column_stmt;

ALTER TABLE ac_skill_publication_attempt
  MODIFY COLUMN sc_version_number VARCHAR(128) NULL,
  ADD COLUMN IF NOT EXISTS skill_version_id BIGINT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS error_code VARCHAR(128) NULL,
  ADD COLUMN IF NOT EXISTS recovery_state VARCHAR(24) NULL,
  ADD COLUMN IF NOT EXISTS recovery_kind VARCHAR(24) NULL,
  DROP COLUMN IF EXISTS failure_code,
  DROP CONSTRAINT IF EXISTS ck_skill_publication_attempt_status,
  DROP CONSTRAINT IF EXISTS ck_skill_publication_recovery_state,
  DROP CONSTRAINT IF EXISTS ck_skill_publication_recovery_kind,
  ADD CONSTRAINT ck_skill_publication_attempt_status
    CHECK (status IN ('PREPARING', 'SC_SUBMITTING', 'WAITING_SC',
      'RESULT_UNKNOWN', 'MATERIALIZING', 'SUCCEEDED', 'FAILED')),
  ADD CONSTRAINT ck_skill_publication_recovery_state
    CHECK (recovery_state IS NULL OR recovery_state IN
      ('AUTO_RETRYING', 'AVAILABLE', 'NOT_AVAILABLE')),
  ADD CONSTRAINT ck_skill_publication_recovery_kind
    CHECK (recovery_kind IS NULL OR recovery_kind IN
      ('PREPARATION', 'SC_STATUS_CHECK', 'MATERIALIZATION'));
