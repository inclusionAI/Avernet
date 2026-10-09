-- Persist Relay RuntimeInfo.actual_goal across instance restarts and
-- cross-instance graph hydration. Existing deployments run this additive
-- migration; fresh CREATE TABLE definitions include the column.
ALTER TABLE `task_node_run_info`
    ADD COLUMN `actual_goal` text DEFAULT NULL COMMENT '实际执行目标 JSON: {objective,acceptances}';
