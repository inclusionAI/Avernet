-- ac_task_execution_graph — auxiliary read-optimized projection of the
-- execution graph (large-payload split). graph TEXT = serialized
-- TaskExecutionGraph; version INT = optimistic-update counter.
-- Prod DDL (OceanBase / MySQL). Keep in sync with the ORM model.
CREATE TABLE IF NOT EXISTS `ac_task_execution_graph` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `env` VARCHAR(64) NOT NULL DEFAULT 'dev',
  `task_id` VARCHAR(128) NOT NULL,
  `graph` TEXT NULL,
  `version` INT NOT NULL DEFAULT 1,
  `gmt_create` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ac_task_execution_graph_task` (`env`, `task_id`),
  KEY `idx_ac_task_execution_graph_env_task` (`env`, `task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;