-- ac_task — task aggregate snapshot (spec + runtime face)
-- Prod DDL (OceanBase / MySQL). Provisioned manually by ops; the local/CI
-- path uses Base.metadata.create_all on SQLite (AutoIncrementBigInteger
-- with_variant). Keep this in sync with ac_task ORM model in
-- core/task/repository/models.py.
CREATE TABLE IF NOT EXISTS `ac_task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `task_id` VARCHAR(128) NOT NULL,
  `env` VARCHAR(64) NOT NULL DEFAULT 'dev',
  `user_id` VARCHAR(128) NOT NULL DEFAULT '',
  `source` VARCHAR(32) NOT NULL DEFAULT 'api',
  `status` VARCHAR(32) NOT NULL DEFAULT 'drafting',
  `loop_round` INT NOT NULL DEFAULT 0,
  ``spec_json` TEXT NULL,
  `execution_graph_json` TEXT NULL,
  `gmt_create` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ac_task_task_id` (`task_id`),
  KEY `idx_ac_task_env_status` (`env`, `status`),
  KEY `idx_ac_task_env_user` (`env`, `user_id`),
  KEY `idx_ac_task_env_uuid` (`env`, `task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;