-- Repair for a mis-provisioned ac_bot_config_manifest_apply.
--
-- WHAT WENT WRONG. 2026_08_31_bot_config_manifest_apply.sql declares two
-- tables: the apply RECORD (ac_bot_config_manifest_apply: apply_id, trigger,
-- status, report, actor, started_at, finished_at) and the apply LOCK
-- (ac_bot_config_manifest_apply_lock: holder_user_id, lock_token, and the
-- UNIQUE KEY that *is* the lock). At least one environment was provisioned with
-- the LOCK's body under the RECORD's name -- SHOW CREATE TABLE
-- ac_bot_config_manifest_apply came back with holder_user_id / lock_token /
-- uk_manifest_apply_lock and the lock's COMMENT, and no apply_id at all.
-- Every read of the record then fails the way GET .../config-manifest/last-apply
-- did: ``1054 Unknown column 'ac_bot_config_manifest_apply.apply_id'``.
--
-- WHAT THIS DOES. Idempotent, and a no-op wherever the schema is already right:
--
--   1. Detect the mis-shape: ac_bot_config_manifest_apply exists but has no
--      apply_id column. Nothing below touches a correctly shaped table.
--   2. Move the mis-shaped table out of the way. It has exactly the lock's
--      shape, so when the lock table is missing it is RENAMED INTO PLACE as
--      ac_bot_config_manifest_apply_lock -- the same DDL, only under the right
--      name now. When the lock table already exists it is renamed ASIDE to
--      ac_bot_config_manifest_apply_misprovisioned_20260907 instead. Nothing is
--      dropped: whatever rows it holds are at most stale lock rows, and the
--      operator decides when to DROP the quarantined copy.
--   3. Create ac_bot_config_manifest_apply with the canonical DDL, verbatim
--      from 2026_08_31_bot_config_manifest_apply.sql (see that file for why
--      every index is GLOBAL, why AUTO_INCREMENT_MODE is pinned to ORDER, and
--      why started_at/finished_at are DATETIME).
--   4. Verify. Save the results with the deployment record.
--
-- Conditional DDL goes through PREPARE/EXECUTE, the pattern
-- skill_center/sql/2026_08_30_finalize_space_skill_group3_publication.sql set,
-- because MySQL/OceanBase have no IF around a bare statement.

-- 1. Is the record table the mis-shaped one? (1 = yes, 0 = no / absent.)
SET @apply_is_misshaped = (
  SELECT COUNT(*) FROM information_schema.TABLES t
   WHERE t.TABLE_SCHEMA = DATABASE()
     AND t.TABLE_NAME = 'ac_bot_config_manifest_apply'
     AND NOT EXISTS (
       SELECT 1 FROM information_schema.COLUMNS c
        WHERE c.TABLE_SCHEMA = t.TABLE_SCHEMA
          AND c.TABLE_NAME = t.TABLE_NAME
          AND c.COLUMN_NAME = 'apply_id'
     )
);

SET @lock_exists = (
  SELECT COUNT(*) FROM information_schema.TABLES
   WHERE TABLE_SCHEMA = DATABASE()
     AND TABLE_NAME = 'ac_bot_config_manifest_apply_lock'
);

-- 2. Move the mis-shaped table out of the way: into place as the lock when the
--    lock is missing, aside under a quarantine name when it is not.
SET @move_misshaped_apply = IF(
  @apply_is_misshaped = 1,
  IF(
    @lock_exists = 0,
    'RENAME TABLE `ac_bot_config_manifest_apply` TO `ac_bot_config_manifest_apply_lock`',
    'RENAME TABLE `ac_bot_config_manifest_apply` TO `ac_bot_config_manifest_apply_misprovisioned_20260907`'
  ),
  'SELECT 1'
);
PREPARE move_misshaped_apply_stmt FROM @move_misshaped_apply;
EXECUTE move_misshaped_apply_stmt;
DEALLOCATE PREPARE move_misshaped_apply_stmt;

-- 3. The apply RECORD, as 2026_08_31_bot_config_manifest_apply.sql declares it.
--    IF NOT EXISTS keeps this a no-op on an environment that was provisioned
--    correctly in the first place.
CREATE TABLE IF NOT EXISTS `ac_bot_config_manifest_apply` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `apply_id`       varchar(64)   NOT NULL COMMENT 'Public handle for this apply',
  `env`            varchar(20)   NOT NULL COMMENT 'Environment: prod/pre/dev',
  `entity_id`      varchar(256)  NOT NULL COMMENT 'Entity id: the bot entity_id',
  `bot_id`         varchar(256)  NOT NULL COMMENT 'Bot ID',
  `trigger`        varchar(32)   NOT NULL COMMENT 'What started it: explicit/create/republish/restart',
  `status`         varchar(16)   NOT NULL COMMENT 'RUNNING, or SUCCEEDED/PARTIAL/FAILED',
  `report`         mediumtext    NOT NULL COMMENT 'The per-entry report (JSON)',
  `actor`          varchar(1024) NOT NULL COMMENT 'Audit: who started it',
  `started_at`     datetime      NOT NULL COMMENT 'When the apply began',
  `finished_at`    datetime      NULL     COMMENT 'When it ended; NULL while RUNNING',
  `avernet_tenant` varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT 'Tenant, for data isolation',
  `gmt_create`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Row created',
  `gmt_modified`   timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Row last modified',
  PRIMARY KEY (`id`),
  KEY `idx_manifest_apply_latest`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `id`) GLOBAL,
  KEY `idx_manifest_apply_by_id`
    (`avernet_tenant`, `env`, `entity_id`, `bot_id`, `apply_id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Bot config manifest apply record';

-- 4. POST: both tables present, each with its own distinguishing column, and
--    no quarantined copy unless step 2 had to leave one. Expected:
--      ac_bot_config_manifest_apply       has_apply_id=1  has_lock_token=0
--      ac_bot_config_manifest_apply_lock  has_apply_id=0  has_lock_token=1
SELECT t.TABLE_NAME,
       SUM(c.COLUMN_NAME = 'apply_id')   AS has_apply_id,
       SUM(c.COLUMN_NAME = 'lock_token') AS has_lock_token
  FROM information_schema.TABLES t
  JOIN information_schema.COLUMNS c
    ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
 WHERE t.TABLE_SCHEMA = DATABASE()
   AND t.TABLE_NAME IN ('ac_bot_config_manifest_apply',
                        'ac_bot_config_manifest_apply_lock',
                        'ac_bot_config_manifest_apply_misprovisioned_20260907')
 GROUP BY t.TABLE_NAME
 ORDER BY t.TABLE_NAME;
