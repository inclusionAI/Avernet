-- Owner-granted bot→app authorizations.
--
-- Two tables, and the split is the design rather than a filing decision. The
-- live table answers "may this app reach this bot right now" — one answer, so
-- it carries a unique key the database enforces. The log answers "when could
-- it, historically" — unboundedly many answers, so it carries no unique key at
-- all. A single soft-deleted table cannot hold both: with a status column in
-- the key, the SECOND withdrawal of one pair collides, and MySQL/OceanBase have
-- no partial index to express "unique among the live rows".
--
-- New tables only; no ALTER, so this applies to an existing database without
-- touching any current row.

CREATE TABLE ac_bot_app_grant (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  app_id BIGINT(20) UNSIGNED NOT NULL
    COMMENT 'gateway avernet_application.id, from the App principal',
  -- 1024: the gateway's app_name is unconstrained at its own boundary. Free to
  -- widen because app_name is in no index here, unlike owner_id below.
  app_name VARCHAR(1024) NOT NULL
    COMMENT 'app display name, snapshotted at consent time',
  bot_id VARCHAR(256) NOT NULL COMMENT 'the authorized bot',
  -- The delegating user: a row means "this app may act as this user on this
  -- bot". Distinct from owner_id below, which they equal only when the
  -- delegator owns the bot.
  --
  -- COLLATE is pinned here and nowhere else in this file on purpose. Every
  -- app-only request resolves on this column, so it must compare byte-exact in
  -- every environment; the deployed table carries utf8mb4_bin where this file's
  -- other columns do not, and an unqualified column would inherit whichever
  -- table default it landed next to. Pinning it removes this column from that
  -- drift without pretending to resolve the rest of it.
  user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL
    COMMENT 'delegating user, resolved server-side',
  owner_id VARCHAR(256) NOT NULL COMMENT 'bot owner, resolved server-side',
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  -- avernet_tenant leads the key for the reason ac_user_mcp_config documents:
  -- a user id only means anything within a tenant, so two tenants may each
  -- hold a "12345" owning a same-named bot.
  --
  -- Keyed on user_id, not owner_id, and not both. Uniqueness is per delegation:
  -- two collaborators may each authorize the same app for the same bot, and
  -- keyed on the owner those would collide, with the idempotent grant path
  -- silently swallowing the second. Carrying both would be 3416 bytes against
  -- InnoDB's 3072 cap (tenant 256 + app_id 8 + bot_id 1024 + user 1024 +
  -- owner 1024 + env 80), so this was never a choice between one and both.
  UNIQUE KEY uk_bot_app_grant_scope
    (avernet_tenant, app_id, bot_id, user_id, env) GLOBAL,
  -- the app's view: which bots may this app reach as this user
  KEY idx_bot_app_grant_app_user
    (avernet_tenant, app_id, user_id, env) GLOBAL,
  -- the bot's view: which apps can reach this bot, and who let them in. Needs
  -- its own index -- the unique key and the index above both put app_id
  -- straight after the tenant, and this listing supplies no app_id to reach
  -- past it. Its (avernet_tenant, bot_id) prefix also serves the sweep that
  -- revokes every grant on a bot when the bot is deleted, which likewise names
  -- no app and no delegating user.
  KEY idx_bot_app_grant_bot_owner
    (avernet_tenant, bot_id, owner_id, env) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'live bot→app authorizations; a row exists iff access is in force';

CREATE TABLE ac_bot_app_grant_log (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  app_id BIGINT(20) UNSIGNED NOT NULL,
  app_name VARCHAR(1024) NOT NULL,
  bot_id VARCHAR(256) NOT NULL,
  -- Free to add here: this table has no unique key, so no byte budget binds it.
  user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL,
  owner_id VARCHAR(256) NOT NULL,
  action VARCHAR(32) NOT NULL COMMENT 'granted | revoked',
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  -- Deliberately NO unique key. This table's job is to accept every event,
  -- including the fourth revocation of the same pair; a constraint here would
  -- reintroduce exactly the collision the two-table split exists to remove.
  --
  -- app_name and avernet_tenant are duplicated rather than joined from the live
  -- table, because the live row is gone by the time a revocation is audited —
  -- which is precisely when this table is read.
  KEY idx_bot_app_grant_log_bot
    (avernet_tenant, bot_id, owner_id, env, gmt_create) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'append-only history of bot→app authorization grants and revocations';
