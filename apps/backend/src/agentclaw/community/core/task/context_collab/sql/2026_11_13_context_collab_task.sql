-- Apply AFTER 2026_11_11_context_collab.sql, before starting the Task-authoritative runtime.
-- OceanBase MySQL mode, matching the baseline dialect. Apply once via the deployment migration process.
-- Stop old snapshot-writing runtimes before cutover; mixed old/new writers are not supported.
-- No historic events or profile versions are rewritten. GET never performs backfill.

ALTER TABLE `ac_context_collab_collet`
  ADD COLUMN `root_task_ref` varchar(128) DEFAULT NULL COMMENT 'Independent root L4 Task; NULL selects explicit legacy decoding';

ALTER TABLE `ac_context_collab_event`
  ADD COLUMN `task_ref` varchar(128) DEFAULT NULL COMMENT 'Work subject; NULL for Message and pre-Task history',
  ADD COLUMN `task_version_after` bigint(20) DEFAULT NULL COMMENT 'Task revision, distinct from context_version_after';

CREATE TABLE `ac_context_collab_task` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `task_id` varchar(128) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `parent_task_ref` varchar(128) DEFAULT NULL,
  `task_version` bigint(20) NOT NULL,
  `task_data` mediumtext NOT NULL COMMENT 'Typed L4 Task, including completion conditions and event provenance; not a read projection',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_task` (`avernet_tenant`, `env`, `task_id`) GLOBAL,
  KEY `idx_context_collab_task_context` (`avernet_tenant`, `env`, `collab_context_id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Independent ACCS L4 work-item identity and revision';
