-- Add node-level Todo execution generations and current-version lookup.
-- Existing executions become generation 1 and remain current until explicitly invalidated.
-- This is a one-time additive migration. Inspect information_schema before retrying
-- a partially applied execution; ODC/OceanBase MySQL mode does not reliably accept
-- standalone CREATE INDEX or IF NOT EXISTS on ALTER members.

ALTER TABLE `ac_todo_execution`
    ADD COLUMN `generation` int(11) NOT NULL DEFAULT 1 COMMENT '阶段内单调递增执行代次' AFTER `stage_instance_id`,
    ADD COLUMN `is_current` tinyint(1) NOT NULL DEFAULT 1 COMMENT '是否为该阶段当前可互动版本' AFTER `generation`,
    ADD COLUMN `invalidated_by_interaction_id` varchar(256) DEFAULT NULL COMMENT '使本代及后续失效的互动事件' AFTER `is_current`,
    ADD COLUMN `replaced_by_execution_id` varchar(64) DEFAULT NULL COMMENT '替代本代的新执行标识' AFTER `invalidated_by_interaction_id`,
    ADD KEY `idx_todo_execution_stage_generation`
        (`avernet_tenant`, `env`, `todo_id`, `stage_instance_id`, `generation`, `id`),
    ADD KEY `idx_todo_execution_current`
        (`avernet_tenant`, `env`, `todo_id`, `is_current`, `stage_instance_id`, `id`);
