-- acli operations (apps/backend/specs/2026-10-21-acli-cli-operations/spec.md).
--
-- One row per acli add, remove or cancel of a bot runtime's CLI. Each runs as
-- an ac_task_queue task; this row links the operation id the API returns to
-- that task (a task handler is never told its task id) and to the operation
-- the engine started (the task payload is fixed at enqueue, so it cannot).
-- The operation's status is the task's, and is not repeated here.
--
-- DIALECT: OCEANBASE, MYSQL MODE, following the sibling DDL in
-- core/bot_config_manifest/sql/: every index GLOBAL, TIMESTAMP for the
-- database-filled audit columns, no ENGINE or storage clauses.

CREATE TABLE `ac_acli_operation` (
  `id`                  bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`                 varchar(20)  NOT NULL COMMENT '环境标识: prod/pre/dev',
  `operation_id`        varchar(32)  NOT NULL COMMENT '操作 ID（API 返回）',
  `task_id`             bigint(20)   NOT NULL COMMENT 'ac_task_queue.id',
  `kind`                varchar(16)  NOT NULL COMMENT 'operation / cancel',
  `action`              varchar(16)  NOT NULL COMMENT 'install / uninstall',
  `cli_code`            varchar(256) NOT NULL COMMENT 'acli 市场名',
  `bot_pk`              bigint(20)   NOT NULL COMMENT 'ac_bots.id',
  `stage`               varchar(16)  NOT NULL COMMENT 'draft / verify / online',
  `target_operation_id` varchar(32)  DEFAULT NULL COMMENT '取消的目标操作 ID',
  `engine_operation_id` varchar(32)  DEFAULT NULL COMMENT '引擎操作 ID',
  `gmt_create`          timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`        timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_operation_id` (`operation_id`) GLOBAL,
  -- The latest operation of a runtime, per kind: the live-operations read.
  KEY `idx_env_bot_stage_kind` (`env`, `bot_pk`, `stage`, `kind`, `id`) GLOBAL
) DEFAULT CHARSET = utf8mb4 COMMENT = 'acli CLI 操作';
