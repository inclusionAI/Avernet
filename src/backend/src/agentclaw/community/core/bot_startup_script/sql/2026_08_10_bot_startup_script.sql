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
-- WHY THAT KEY IS STABLE FOR THE LIFE OF THE ROW, which is what lets every read
-- here be a plain lookup with no ownership check on top:
--
--   * ac_bots carries UNIQUE KEY uk_bot_id_entity_id_env (bot_id, entity_id,
--     env), and is_delete is NOT part of it;
--   * bot deletion is a soft update — nothing hard-deletes an ac_bots row — so
--     a deleted bot keeps occupying that tuple.
--
-- So one (env, entity_id, bot_id) names at most one ac_bots row, ever. A script
-- row cannot be inherited by a later bot, because there is no later bot: an
-- attempt to re-create the identifier collides with the surviving row. Deleting
-- the script when its bot is deleted is therefore hygiene (executable content
-- should not outlive its owner), not a safety property other code depends on.
--
-- If uk_bot_id_entity_id_env is ever dropped or narrowed, that reasoning goes
-- with it and this table needs an owner stamp again — the read path would then
-- be resolving an identifier that can change hands, and the body it returns is
-- executed on every container start.
--
-- The ac_bots ORM model declares that key too, as
-- uk_bot_id_entity_id_env_tenant, so the create_all schema used locally and by
-- singlebox enforces it rather than relying on prod's out-of-band DDL. It is
-- tenant-scoped where prod's may or may not be: adding the tenant can only
-- accept more than prod does, never less, and it still matches this table's own
-- key exactly. See plugin_api/models.py.
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
  -- varchar(256) rather than char(64), matching the deployed table. The value
  -- is always exactly 64 lowercase hex characters -- the column is wider than
  -- anything that can be stored in it, and deliberately so: this file has to
  -- read back identically to ``SHOW CREATE TABLE``, or the next transcription
  -- error hides in the diff between them the way the mediumint/mediumtext one
  -- did. CHAR's blank-padding is not relied on anywhere: the repository writes
  -- a hex digest and compares it for equality.
  `script_key`    varchar(256)  NOT NULL COMMENT '唯一键代理：sha256(env|entity_id|bot_id)',
  -- TIMESTAMP, not DATETIME, and the split is the module rule the manifest
  -- DDL states: a column the *database* fills round-trips through TIMESTAMP's
  -- session-offset conversion unchanged, while one the *application* binds
  -- would be stored shifted. Both of these are database-filled -- the ORM
  -- maps them to ``func.now()``, which renders server-side as ``now()``, and
  -- the upsert re-stamps gmt_modified the same way -- so TIMESTAMP is the
  -- correct half of that rule and matches ac_bots.
  `gmt_create`    timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  -- The index every read uses: the repository filters on script_key rather
  -- than on the three columns it hashes, so there is no second lookup key here
  -- to drift out of step with the ORM model.
  --
  -- WHY THE ``_v2`` SUFFIX ON A TABLE THAT HAS NO v1. The name a fresh table
  -- would want, ``uk_tenant_script_key``, is occupied in the already-deployed
  -- environments by a key on (avernet_tenant, env, entity_id, bot_id) -- the
  -- pre-surrogate design, carrying this key's name but not its columns -- and
  -- that key cannot be dropped there. So the surrogate key is added alongside
  -- it under this name, and this file uses the same name so that ONE name is
  -- correct in every environment and in the ORM model. A fresh table gets only
  -- this key; a migrated one carries the legacy key too, which is redundant
  -- rather than wrong: script_key is injective over (env, entity_id, bot_id),
  -- so both enforce the same logical uniqueness, and either may be the one
  -- that raises the IntegrityError the repository's upsert retries on.
  UNIQUE KEY `uk_tenant_script_key_v2` (`avernet_tenant`, `script_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 启动脚本';
