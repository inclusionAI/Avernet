-- Per-bot CLI tools (W9, issue #1477).
--
-- One row per installed command. This table is the platform's record of what a
-- bot has, and it is what makes a manifest apply's full override decidable:
-- removals are computed from these rows, not from whatever an engine reports.
--
-- NO COLUMN HOLDS A CONTAINER PATH. Every CLI operation addresses a tool by
-- name and the engine owns placement — where the file lands, its executable
-- bit and how the agent reaches it are all inside the engine's install call.
-- `oss_key` is the *platform's own* object key, where we kept the bytes so a
-- teclaw artifact can reference them; it is not a location in any container.
--
-- Key is (avernet_tenant, tool_key), where tool_key is a sha256 of the logical
-- key (env, entity_id, bot_id, name), for the reasons ac_bot_startup_script
-- records at length: entity_id alone is 4096 utf8mb4 bytes, past InnoDB's
-- 3072-byte index-key cap, and the tenant is in the key because ac_bots is
-- itself tenant-scoped so a bot_id is unique only within a tenant. Without the
-- tenant here, two colliding bots would share a row that names an executable
-- which then runs in the other tenant's container.
--
-- The stability argument for that key is the same one that table documents:
-- ac_bots carries UNIQUE KEY uk_bot_id_entity_id_env and deletion there is a
-- soft update, so one (env, entity_id, bot_id) names at most one bot ever and a
-- tool row cannot be inherited by a later bot.
--
-- entity_id is a storage key only: it is resolved server-side from the bot
-- record and is never a request parameter or a response field on the public API.
CREATE TABLE `ac_bot_cli_tool` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  -- 1024, matching ac_bots.entity_id exactly. It is NOT in the uniqueness key
  -- (see tool_key below), so it is free to match its source.
  `entity_id`     varchar(1024) NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  `name`          varchar(128)  NOT NULL COMMENT '命令名（bot 内唯一）',
  `source`        text          NOT NULL COMMENT '声明的来源 URL / 命名源',
  -- The user-declared pin. Mandatory for every non-git form: the platform will
  -- not distribute an unpinned executable on a caller's behalf.
  `digest`        varchar(80)   NOT NULL COMMENT '用户钉扎的 sha256:…',
  -- Which member of the fetched archive is the tool. A SOURCE-side path, never
  -- a target one. It is part of the convergence key alongside digest: the same
  -- archive with a different subpath delivers a different file under the same
  -- command name, and keying on digest alone would report that unchanged.
  `subpath`       varchar(512)  DEFAULT NULL COMMENT '归档内选中的成员路径',
  -- Platform-computed over the finally selected file. The engine's change
  -- test; never the store's ETag, which is not a content MD5 for a multipart
  -- upload.
  `md5`           char(32)      NOT NULL COMMENT '平台对最终文件计算的 MD5',
  `size_bytes`    bigint(20)    NOT NULL COMMENT '最终文件字节数',
  `version`       varchar(64)   DEFAULT NULL COMMENT '元数据，不参与收敛',
  `oss_key`       varchar(512)  NOT NULL COMMENT '平台保存字节的对象键（非容器路径）',
  -- 'manifest' or the installing user's id, so a full override can report that
  -- it replaced an API-installed tool instead of silently overwriting it.
  -- 1024 to match modifier: both hold the same acting-user principal, and a
  -- narrower width would fail the upsert after the tool is already installed.
  `installed_by`  varchar(1024) NOT NULL COMMENT '安装者：manifest 或用户 ID',
  `modifier`      varchar(1024) NOT NULL COMMENT '审计：最后写入者',
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  `tool_key`      char(64)      NOT NULL COMMENT '唯一键代理：sha256(env|entity_id|bot_id|name)',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- A command name is unwritable twice for one bot: two concurrent installs of
  -- the same name cannot both land, whichever order they arrive in.
  UNIQUE KEY `uk_tenant_cli_tool_key` (`avernet_tenant`, `tool_key`),
  -- Listing a bot's tools is the hot read (every apply, every compose). The
  -- tenant leads because the guard appends `avernet_tenant = ?` to every SELECT
  -- on this model, so a tenant-trailing index would make it a residual filter —
  -- the sibling manifest_content/manifest_apply indexes lead with it for the
  -- same reason.
  --
  -- entity_id is deliberately NOT in the index, unlike those siblings: theirs
  -- is varchar(256), this one matches ac_bots at varchar(1024), and 1024
  -- utf8mb4 characters is 4096 bytes on its own — past InnoDB's 3072-byte
  -- index-key cap. It stays a residual filter over the handful of rows one
  -- (tenant, env, bot_id) already selects.
  KEY `idx_tenant_env_bot` (`avernet_tenant`, `env`, `bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot CLI 工具';
