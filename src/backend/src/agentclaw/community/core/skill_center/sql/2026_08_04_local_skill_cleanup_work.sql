-- RELEASE GATE: deploy this DDL and verify it before deploying application
-- code that can commit Local Skill replacement.  The application has no
-- compatibility fallback for an absent cleanup-work table.
-- Strict Bot scope (env, owner_id, bot_id) is deployment-wide unique, therefore
-- this operational table intentionally is not a tenant-leading catalog.
CREATE TABLE `ac_local_skill_cleanup_work` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `env` VARCHAR(20) NOT NULL,
  `owner_id` VARCHAR(128) NOT NULL,
  `bot_id` VARCHAR(100) NOT NULL,
  `skill_id` BIGINT NOT NULL,
  `package_locator` VARCHAR(1024) NOT NULL,
  `package_locator_hash` CHAR(64) NOT NULL,
  `requires_runtime_restore` TINYINT(1) NOT NULL DEFAULT 0,
  `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  `attempts` INT NOT NULL DEFAULT 0,
  `last_error` TEXT NULL,
  `cleaned_at` TIMESTAMP NULL,
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_local_skill_cleanup_scope_locator_hash` (`env`, `owner_id`, `bot_id`, `package_locator_hash`),
  KEY `idx_local_skill_cleanup_pending` (`env`, `status`, `gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Verify before application rollout:
--   SHOW CREATE TABLE ac_local_skill_cleanup_work;
--   SELECT COUNT(*) FROM information_schema.statistics
--     WHERE table_schema = DATABASE()
--       AND table_name = 'ac_local_skill_cleanup_work'
--       AND index_name IN ('uk_local_skill_cleanup_scope_locator_hash',
--                          'idx_local_skill_cleanup_pending');
-- The unique key is bounded at 20 + 128 + 100 utf8mb4 characters plus a
-- 64-byte ASCII SHA-256 digest (under 1,056 maximum bytes), rather than an
-- unbounded 1,024-character locator. Verify target limits before rollout:
--   SELECT @@version, @@character_set_server;
--   SHOW CREATE TABLE ac_local_skill_cleanup_work;
-- Roll back only before application deployment: DROP TABLE ac_local_skill_cleanup_work;
-- After code rollout, retain work rows until they reach status='cleaned'; use
-- a forward repair rather than dropping the table, so no obsolete bytes lose
-- their durable retry record.
