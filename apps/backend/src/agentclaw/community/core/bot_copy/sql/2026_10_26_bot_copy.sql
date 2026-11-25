-- ac_bot_copy: bot-copy operation ledger + state machine
-- Spec §7.5.3 — applied once out-of-band on prod OceanBase / pre.
-- singlebox + tests build it via Base.metadata.create_all (the ORM model in
-- repository/models.py), so this file is the prod/pre DDL only.
-- Format aligned with core/service_bot/sql/ac_bot_publish.sql and
-- core/skill_center/sql/2026_09_21_bot_mcp_config.sql: AUTO_INCREMENT, all
-- indexes GLOBAL, DEFAULT CHARSET=utf8mb4, utf8mb4_bin on id/bot_id columns.

CREATE TABLE `ac_bot_copy` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `operation_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT 'FE polling correlation id',
  `source_bot_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '源 ac_bots.bot_id',
  `source_owner_id` varchar(1024) NOT NULL COMMENT '源 ac_bots.owner_id',
  `dst_bot_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '新建 dst ac_bots.bot_id',
  `dst_owner_id` varchar(1024) NOT NULL COMMENT 'dst ac_bots.owner_id;operator 多数同 dst_owner_id',
  `operator_id` varchar(1024) NOT NULL COMMENT 'trigger actor;collaborator 场景可与 dst_owner_id 不等',
  `status` varchar(24) NOT NULL DEFAULT 'INITIAL' COMMENT 'INITIAL/CREATED/CONFIG_COPIED/FILE_SYNCED/BOT_STARTED/CAPSET_SYNCED/CONVERGED/AUTH_PENDING/FAILED',
  `request_detail` text COMMENT 'JSON: request-side params blob (excludes + 后续复制额外参数,无 per-param 新列)',
  `progress_ext` text COMMENT 'JSON: step 中间态 resync',
  `retries` int(11) NOT NULL DEFAULT '0' COMMENT 'step-level attempt count',
  `last_retry_at` timestamp NULL DEFAULT NULL COMMENT 'operator retry CAS throttle: last_retry_at < now-60s',
  `last_error` text COMMENT '终态 FAILED last exception summary',
  `env` varchar(20) NOT NULL COMMENT 'prod/pre/dev/singlebox;全查询 filter',
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw' COMMENT 'data-隔离 tenant',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_operation_id` (`env`, `operation_id`) GLOBAL,
  KEY `idx_env_dst_bot_id` (`env`, `dst_bot_id`) GLOBAL,
  KEY `idx_env_status_last_retry_at` (`env`, `status`, `last_retry_at`) GLOBAL,
  KEY `idx_env_operator_status` (`env`, `operator_id`, `status`) GLOBAL
) DEFAULT CHARSET = utf8mb4 COMMENT = 'bot-copy operation ledger + state machine';
