-- Add the BaaS-owned public interaction ID to existing interaction tables.
-- Existing rows keep the interactionId previously exposed to callers so an
-- in-flight interaction remains resolvable across deployment.

ALTER TABLE `baas_bot_run_interaction`
ADD COLUMN IF NOT EXISTS `baas_interaction_id` varchar(160) DEFAULT NULL
COMMENT 'BaaS public interactionId' AFTER `id`;

UPDATE `baas_bot_run_interaction`
SET `baas_interaction_id` = `interaction_id`
WHERE `baas_interaction_id` IS NULL;

ALTER TABLE `baas_bot_run_interaction`
MODIFY COLUMN `baas_interaction_id` varchar(160) NOT NULL
COMMENT 'BaaS public interactionId';

-- This intentionally fails if historical Engine interaction IDs collide.
-- Such rows require operator reconciliation instead of ambiguous resolution.
ALTER TABLE `baas_bot_run_interaction`
ADD UNIQUE KEY IF NOT EXISTS `uk_baas_interaction_id` (`baas_interaction_id`);
