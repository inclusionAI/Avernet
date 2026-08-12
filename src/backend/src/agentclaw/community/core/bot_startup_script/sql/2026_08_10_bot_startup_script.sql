-- Per-bot startup script (issue #926).
--
-- One row per bot at most. The body is appended to the container start
-- sequence the backend composes in ``BaasService._get_start_cmd``; clearing the
-- script deletes the row rather than storing an empty body, so "no row" and
-- "no script" are the same state.
--
-- Key is (avernet_tenant, script_key), where script_key is a sha256 of the
-- logical key (env, entity_id, bot_id). The tenant is in the key because
-- ac_bots is itself tenant-scoped, so a bot_id is unique only within a tenant —
-- legacy "default" bots carry documented residual cross-tenant collision on
-- that identifier. Without the tenant here, two such bots would share one
-- script row and each tenant could overwrite the other's script, which then
-- executes in the other's container.
--
-- entity_id is a storage key only: it is resolved server-side from the bot
-- record and is never a request parameter or a response field on the public API.
CREATE TABLE `ac_bot_startup_script` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  -- 1024, matching ac_bots.entity_id exactly. It is NOT in the uniqueness key
  -- (see script_key below), so it is free to match its source rather than being
  -- narrowed to fit an index.
  `entity_id`     varchar(1024) NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  `script`        mediumtext    NOT NULL COMMENT '脚本正文（清空即删行）',
  `size_bytes`    int(11)       NOT NULL COMMENT '脚本正文字节数（UTF-8）',
  `modifier`      varchar(1024) NOT NULL COMMENT '审计：最后写入者',
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  -- Bounded surrogate for the logical key (env, entity_id, bot_id). entity_id
  -- alone is 4096 utf8mb4 bytes, past InnoDB's 3072-byte index-key cap, so the
  -- constraint is carried on a fixed-width sha256 hex digest instead. Written
  -- by the repository; the tenant is carried alongside rather than hashed in,
  -- so the isolation boundary stays visible in the key.
  `script_key`    char(64)      NOT NULL COMMENT '唯一键代理：sha256(env|entity_id|bot_id)',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The only index, and every read uses it: the repository filters on
  -- script_key rather than on the three columns it hashes, so there is no
  -- second lookup key here to drift out of step with the ORM model.
  UNIQUE KEY `uk_tenant_script_key` (`avernet_tenant`, `script_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 启动脚本';
