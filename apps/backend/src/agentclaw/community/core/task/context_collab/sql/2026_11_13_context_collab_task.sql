-- Apply AFTER 2026_11_11_context_collab.sql, before starting the Task-authoritative runtime.
-- OceanBase MySQL mode, matching the baseline dialect. Apply once via the deployment migration process.
-- Stop old snapshot-writing runtimes before cutover; mixed old/new writers are not supported.
-- Work Event subject/revision stays inside ac_context_collab_event.payload.__task_subject;
-- the baseline Event table must NOT gain task_ref/task_version_after physical columns.
-- The root Task pointer lives inside ac_context_collab_collet.context_spec, not a physical Collet column.

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
