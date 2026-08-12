-- Per-bot startup script (issue #926).
--
-- One row per bot at most. The body is appended to the container start
-- sequence the backend composes in ``BaasService._get_start_cmd``; clearing the
-- script deletes the row rather than storing an empty body, so "no row" and
-- "no script" are the same state.
--
-- Key is (env, entity_id, bot_id), matching ac_bot_restart_lock — the existing
-- per-bot side table. entity_id is a storage key only: it is resolved
-- server-side from the bot record and is never a request parameter or a
-- response field on the public API.
CREATE TABLE `ac_bot_startup_script` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`     varchar(1024) NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  `script`        mediumtext    NOT NULL COMMENT '脚本正文（清空即删行）',
  `size_bytes`    int(11)       NOT NULL COMMENT '脚本正文字节数（UTF-8）',
  `modifier`      varchar(1024) NOT NULL COMMENT '审计：最后写入者',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_entity_id_bot_id` (`env`, `entity_id`, `bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 启动脚本';
