-- 2026-04-24: add engine_type dimension to skill set tables.
-- After running: verify back-fill with
--   SELECT engine_type, COUNT(*) FROM ac_skill_set WHERE is_default = 1 GROUP BY engine_type;

ALTER TABLE `ac_skill_set`
  ADD COLUMN `engine_type` VARCHAR(32) DEFAULT NULL COMMENT '引擎类型，is_default 行由应用层保证非空',
  ADD KEY `idx_engine_type`(`engine_type`) GLOBAL;

-- Back-fill every existing default row with the owning bot's active_engine.
-- Non-default rows stay NULL (they are engine-agnostic today).
UPDATE `ac_skill_set` ss
JOIN `ac_bots` b ON ss.bolt_id = b.bot_id AND ss.env = b.env
SET ss.engine_type = COALESCE(b.active_engine, 'openclaw')
WHERE ss.is_default = 1 AND ss.engine_type IS NULL;

-- is_default=1 rows that have no matching bot row fall back to openclaw.
UPDATE `ac_skill_set`
SET `engine_type` = 'openclaw'
WHERE `is_default` = 1 AND `engine_type` IS NULL;

ALTER TABLE `ac_user_default_skill_set`
  ADD COLUMN `engine_type` VARCHAR(32) DEFAULT NULL COMMENT '引擎类型',
  ADD KEY `idx_udss_engine_type`(`engine_type`) GLOBAL;

-- Back-fill: user-default rows inherit the bot's active_engine.
UPDATE `ac_user_default_skill_set` u
JOIN `ac_bots` b ON u.bolt_id = b.bot_id AND u.env = b.env
SET u.engine_type = COALESCE(b.active_engine, 'openclaw')
WHERE u.engine_type IS NULL;

UPDATE `ac_user_default_skill_set`
SET `engine_type` = 'openclaw'
WHERE `engine_type` IS NULL;

-- Replace the old uniqueness with an engine-aware one.
ALTER TABLE `ac_user_default_skill_set`
  DROP INDEX `uix_user_default_skill_set_user_bolt_env`,
  ADD UNIQUE KEY `uix_user_default_skill_set_user_bolt_engine_env`(`user_id`, `bolt_id`, `engine_type`, `env`) GLOBAL;
