-- task_action_log: append-only high-volume task/node action history.
-- Operator-provisioned in dev/pre/prod; SQLite tests use ORM metadata.
CREATE TABLE IF NOT EXISTS `task_action_log` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    `event_id` varchar(256) NOT NULL,
    `task_id` varchar(128) NOT NULL,
    `node_id` varchar(128) NOT NULL,
    `seq` int NOT NULL,
    `action` varchar(64) NOT NULL,
    `loop_round` int DEFAULT NULL,
    `attempt` int NOT NULL DEFAULT 0,
    `status_from` varchar(64) DEFAULT NULL,
    `status_to` varchar(64) DEFAULT NULL,
    `payload` text NOT NULL,
    `instance_id` varchar(256) DEFAULT NULL,
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_action_event` (`event_id`),
    UNIQUE KEY `uk_task_node_action_seq` (`task_id`, `node_id`, `seq`),
    KEY `idx_task_action_task_node` (`task_id`, `node_id`, `seq`),
    KEY `idx_task_action_created` (`gmt_create`)
) DEFAULT CHARSET = utf8mb4 COMMENT='Task node action history';
