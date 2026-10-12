-- Apply before enabling the Backend task intake API in an existing deployment.
-- The confirmation session is durable; it is not a task until /start succeeds.
CREATE TABLE IF NOT EXISTS `task_intake_session` (
    `session_id` varchar(128) NOT NULL,
    `owner_user_id` varchar(256) NOT NULL,
    `status` varchar(32) NOT NULL,
    `result_json` text NOT NULL,
    `version` bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (`session_id`),
    KEY `idx_task_intake_owner` (`owner_user_id`, `status`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务澄清会话状态';

ALTER TABLE `task_info`
    ADD COLUMN `intake_session_id` varchar(128) DEFAULT NULL;
CREATE UNIQUE INDEX `uk_task_info_intake_session` ON `task_info` (`intake_session_id`);

CREATE TABLE IF NOT EXISTS `task_delivery_intent` (
    `id` bigint NOT NULL AUTO_INCREMENT,
    `task_id` varchar(128) NOT NULL,
    `node_id` varchar(128) NOT NULL,
    `relay_token` varchar(64) NOT NULL,
    `state` varchar(32) NOT NULL DEFAULT 'PENDING',
    `attempt` int NOT NULL DEFAULT 0,
    `lease_until_ms` bigint DEFAULT NULL,
    `lease_token` varchar(64) DEFAULT NULL,
    `last_error` text DEFAULT NULL,
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_delivery_intent` (`task_id`, `node_id`),
    KEY `idx_task_delivery_pending` (`state`, `lease_until_ms`)
) DEFAULT CHARSET = utf8mb4 COMMENT='接力单节点待投递意图';
