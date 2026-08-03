-- Deploy before application code that can commit Local Skill replacement.
-- Strict Bot scope (env, owner_id, bot_id) is deployment-wide unique, therefore
-- this operational table intentionally is not a tenant-leading catalog.
CREATE TABLE `ac_local_skill_cleanup_work` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `env` VARCHAR(20) NOT NULL,
  `owner_id` VARCHAR(128) NOT NULL,
  `bot_id` VARCHAR(100) NOT NULL,
  `skill_id` BIGINT NOT NULL,
  `package_locator` VARCHAR(1024) NOT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  `attempts` INT NOT NULL DEFAULT 0,
  `last_error` TEXT NULL,
  `cleaned_at` TIMESTAMP NULL,
  `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_local_skill_cleanup_scope_locator` (`env`, `owner_id`, `bot_id`, `package_locator`),
  KEY `idx_local_skill_cleanup_pending` (`env`, `status`, `gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Verify: SHOW CREATE TABLE ac_local_skill_cleanup_work;
-- Roll back before code deployment only: DROP TABLE ac_local_skill_cleanup_work;
