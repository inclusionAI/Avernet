-- ac_task_event — append-only event log; single writer of the monotonic seq.
-- No gmt_modified: events are immutable once appended.
-- Prod DDL (OceanBase / MySQL). Keep in sync with ac_task_event ORM model.
CREATE TABLE IF NOT EXISTS `ac_task_event` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `env` VARCHAR(64) NOT NULL DEFAULT 'dev',
  `task_id` VARCHAR(128) NOT NULL,
  `seq` INT NOT NULL,
  `kind` VARCHAR(64) NOT NULL,
  `reported` INT NOT NULL DEFAULT 0,
  `payload_json` TEXT NULL,
  `gmt_create` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ac_task_event_seq` (`env`, `task_id`, `seq`),
  KEY `idx_ac_task_event_env_task_seq` (`env`, `task_id`, `seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;