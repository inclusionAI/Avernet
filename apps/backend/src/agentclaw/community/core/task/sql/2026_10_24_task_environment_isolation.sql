-- Environment isolation for durable Relay handoff state.
-- Apply manually through ODC before deploying a build that writes task_info.env
-- or task_delivery_intent.env. All existing rows are pre-release tasks, so the
-- one-time backfill uses DEFAULT 'pre'.
ALTER TABLE `task_info`
    ADD COLUMN `env` varchar(32) NOT NULL DEFAULT 'pre',
    ADD KEY `idx_task_info_env_status` (`env`, `status`, `gmt_modified`);

ALTER TABLE `task_delivery_intent`
    ADD COLUMN `env` varchar(32) NOT NULL DEFAULT 'pre',
    ADD KEY `idx_task_delivery_env_pending` (`env`, `state`, `lease_until_ms`);
