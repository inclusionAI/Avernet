-- Build-active guard.
--
-- One row per (env, entity_id, bot_id) means "a publish build is reading this
-- Bot's source workspace". An Arca restart checks the row before destroying the
-- instance: a build rsyncs from a workspace that lives inside the container the
-- restart would replace, so overlapping them can make the rsync source vanish.
--
-- The build inserts the row before its transfer and clears it in ``finally``.
-- ``build_lease`` fences clear/renew: a build whose row was replaced after the
-- TTL must not delete or renew its successor's marker. A heartbeat renews
-- ``gmt_create`` while a long transfer is still running, so the TTL is a
-- crash-only fallback rather than a hard cap on build duration.
--
-- entity_id and bot_id are varchar(256): all three key columns are inside the
-- UNIQUE key, and the key must stay within InnoDB's 3072-byte utf8mb4 limit
-- (env 20 + entity_id 256 + bot_id 256 = 532 chars = 2128 bytes).
--
-- The community/prod profile provisions schema out of band
-- (``database.create_schema: false``), so this table must be applied before the
-- code that reads it is deployed. Local/singlebox/test builds create it from
-- the ORM model.
CREATE TABLE `ac_bot_build_active` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`     varchar(256)  NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  `build_lease`   varchar(256)  NOT NULL COMMENT '构建令牌（清理/续租时比对）',
  `gmt_create`    timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（TTL 起算点，心跳续租）',
  `gmt_modified`  timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_build_active_env_entity_id_bot_id` (`env`, `entity_id`, `bot_id`) GLOBAL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='构建活跃守卫';