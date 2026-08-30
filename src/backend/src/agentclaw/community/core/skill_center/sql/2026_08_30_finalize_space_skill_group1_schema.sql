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
  ADD COLUMN IF NOT EXISTS draft_request_id VARCHAR(128) NULL COMMENT '当前升级 Draft 幂等请求身份',
  ADD COLUMN IF NOT EXISTS offline_at TIMESTAMP NULL COMMENT 'TeamClaw 本地可恢复下线时间',
  ADD COLUMN IF NOT EXISTS offline_by VARCHAR(128) NULL COMMENT 'TeamClaw 本地可恢复下线操作者',
  DROP COLUMN IF EXISTS retired_at,
  DROP COLUMN IF EXISTS retired_by;

CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_creation_request
  ON ac_skill (avernet_tenant, env, creation_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_draft_request
  ON ac_skill (avernet_tenant, env, draft_request_id);

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
