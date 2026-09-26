-- Align the seven explicit Space Skill timestamps with the existing gmt_* fields.
--
-- Preconditions:
--   * Run against the TeamClaw database while the database/session time zone is
--     Asia/Shanghai, matching the existing gmt_created/gmt_modified columns.
--   * This is a forward-only schema repair. Existing DATETIME literals are not
--     backfilled; only writes after the matching application deployment are in scope.
--
-- The application writes each of these fields with CURRENT_TIMESTAMP after this
-- migration. Keeping them as TIMESTAMP makes storage and reads match gmt_*.

ALTER TABLE ac_skill_version
  MODIFY COLUMN published_at TIMESTAMP NULL DEFAULT NULL;

ALTER TABLE ac_skill_publication_attempt
  MODIFY COLUMN sc_post_started_at TIMESTAMP NULL DEFAULT NULL,
  MODIFY COLUMN sc_accepted_at TIMESTAMP NULL DEFAULT NULL,
  MODIFY COLUMN completed_at TIMESTAMP NULL DEFAULT NULL;

ALTER TABLE ac_skill
  MODIFY COLUMN offline_at TIMESTAMP NULL DEFAULT NULL COMMENT 'TeamClaw 本地可恢复下线时间';

ALTER TABLE ac_skill_grant
  MODIFY COLUMN revoked_at TIMESTAMP NULL DEFAULT NULL;

ALTER TABLE ac_skill_draft_edit_lease
  MODIFY COLUMN acquired_at TIMESTAMP NULL DEFAULT NULL;
