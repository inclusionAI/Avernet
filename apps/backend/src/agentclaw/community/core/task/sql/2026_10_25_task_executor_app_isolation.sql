-- Executor-application isolation for durable Relay handoff state.
-- Apply manually through ODC before deploying a build whose pending-intent
-- scanners or task recovery run in both agentclaw and agentclawengine.
-- Prerequisite: apply 2026_10_24_task_environment_isolation.sql first.
-- All existing Relay rows were created by agentclawengine, so the backfill
-- defaults historical rows to that executor.
ALTER TABLE `task_info`
    ADD COLUMN `executor_app` varchar(32) NOT NULL DEFAULT 'agentclawengine'
        COMMENT '执行应用:agentclaw|agentclawengine',
    ADD KEY `idx_task_info_executor_app_env_status`
        (`executor_app`, `env`, `status`, `gmt_modified`);

ALTER TABLE `task_delivery_intent`
    ADD COLUMN `executor_app` varchar(32) NOT NULL DEFAULT 'agentclawengine'
        COMMENT '执行应用:agentclaw|agentclawengine',
    ADD KEY `idx_task_delivery_executor_app_env_pending`
        (`executor_app`, `env`, `state`, `lease_until_ms`);
