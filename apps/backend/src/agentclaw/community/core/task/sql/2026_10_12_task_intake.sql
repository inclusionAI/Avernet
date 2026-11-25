-- Apply before enabling the Backend task intake API in an existing deployment.
-- The confirmation session is durable; it is not a task until /start succeeds.
CREATE TABLE IF NOT EXISTS `task_intake_session` (
    `session_id` varchar(128) NOT NULL,
    `owner_user_id` varchar(256) NOT NULL,
    `status` varchar(32) NOT NULL,
    `result_json` text NOT NULL,
    `version` bigint NOT NULL DEFAULT 0,
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`session_id`),
    KEY `idx_task_intake_owner` (`owner_user_id`, `status`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务澄清会话状态';

-- ODC's OceanBase MySQL-mode parser cannot reliably classify a standalone
-- CREATE UNIQUE INDEX statement. Keep the index in ALTER TABLE syntax so the
-- statement stays on the MySQL parser path. This is a one-time migration;
-- inspect information_schema before retrying a partially applied execution.
ALTER TABLE `task_info`
    ADD COLUMN `intake_session_id` varchar(128) DEFAULT NULL,
    ADD UNIQUE KEY `uk_task_info_intake_session` (`intake_session_id`);

CREATE TABLE IF NOT EXISTS `task_delivery_intent` (
    `id` bigint NOT NULL AUTO_INCREMENT,
    `task_id` varchar(128) NOT NULL,
    `node_id` varchar(128) NOT NULL,
    `relay_token` varchar(64) NOT NULL,
    `env` varchar(32) NOT NULL DEFAULT 'pre',
    `executor_app` varchar(32) NOT NULL DEFAULT 'agentclawengine',
    `state` varchar(32) NOT NULL DEFAULT 'PENDING',
    `attempt` int NOT NULL DEFAULT 0,
    `lease_until_ms` bigint DEFAULT NULL,
    `lease_token` varchar(64) DEFAULT NULL,
    `last_error` text DEFAULT NULL,
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_delivery_intent` (`task_id`, `node_id`),
    KEY `idx_task_delivery_pending` (`state`, `lease_until_ms`),
    KEY `idx_task_delivery_env_pending` (`env`, `state`, `lease_until_ms`),
    KEY `idx_task_delivery_executor_app_env_pending` (`executor_app`, `env`, `state`, `lease_until_ms`)
) DEFAULT CHARSET = utf8mb4 COMMENT='接力单节点待投递意图';
