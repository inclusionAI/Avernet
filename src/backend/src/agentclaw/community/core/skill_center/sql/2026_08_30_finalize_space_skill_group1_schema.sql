-- Phase 2 Group 1 additive convergence for databases that already applied F01.
--
-- The obsolete retired_at/retired_by/failure_code physical columns may still
-- exist on an upgraded database until a separately scheduled cleanup. They are
-- deliberately absent from the ORM and public contracts; no write or read path
-- may use them. Fresh databases receive only the final columns from F01.

ALTER TABLE ac_skill
  ADD COLUMN IF NOT EXISTS draft_source_kind VARCHAR(32) NULL COMMENT 'FOLDER/GIT/PUBLISHED_VERSION',
  ADD COLUMN IF NOT EXISTS creation_request_id VARCHAR(128) NULL COMMENT '创建幂等请求身份',
  ADD COLUMN IF NOT EXISTS creation_request_hash VARCHAR(64) NULL COMMENT '创建命令指纹',
  ADD COLUMN IF NOT EXISTS draft_request_id VARCHAR(128) NULL COMMENT '当前升级 Draft 幂等请求身份',
  ADD COLUMN IF NOT EXISTS offline_at TIMESTAMP NULL COMMENT 'TeamClaw 本地可恢复下线时间',
  ADD COLUMN IF NOT EXISTS offline_by VARCHAR(128) NULL COMMENT 'TeamClaw 本地可恢复下线操作者';

CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_creation_request
  ON ac_skill (avernet_tenant, env, creation_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_draft_request
  ON ac_skill (avernet_tenant, env, draft_request_id);

ALTER TABLE ac_skill_publication_attempt
  MODIFY COLUMN sc_version_number VARCHAR(128) NULL,
  ADD COLUMN IF NOT EXISTS skill_version_id BIGINT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS error_code VARCHAR(128) NULL,
  ADD COLUMN IF NOT EXISTS recovery_state VARCHAR(24) NULL,
  ADD COLUMN IF NOT EXISTS recovery_kind VARCHAR(24) NULL;
