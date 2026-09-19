-- User-level application delegations.
--
-- A row in ac_user_app_grant means "app A may act as user U where no bot is
-- addressed". It is the consent the public API admits an application on for
-- the operations that act for a user before any bot exists — creating a bot,
-- above all — where a bot grant (ac_bot_app_grant) has nothing to name. It
-- reaches no existing bot on its own: a bot the application creates under it
-- is granted separately, as an ordinary row in ac_bot_app_grant, so the bot's
-- owner sees and can withdraw that access on the bot like any other.
--
-- Same two-table shape as the bot grant and for the same reason: the live
-- table answers "may this app act as this user right now" — one answer, so a
-- unique key the database enforces — and the log answers "when could it" —
-- unboundedly many, so no unique key at all.
--
-- New tables only; no ALTER, so this applies to an existing database without
-- touching any current row. Must agree column for column with
-- core/bot_app_grant/models.py (UserAppGrantModel, UserAppGrantLogModel).

CREATE TABLE ac_user_app_grant (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  app_id BIGINT(20) UNSIGNED NOT NULL
    COMMENT 'gateway avernet_application.id, from the App principal',
  app_name VARCHAR(1024) NOT NULL
    COMMENT 'app display name, snapshotted at consent time',
  -- The delegating user: every app-only request on a user-delegated operation
  -- resolves on this column, so it is pinned byte-exact as the bot grant's is.
  user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL
    COMMENT 'delegating user, resolved server-side',
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  -- One delegation per (tenant, app, user, env). The tenant leads because a
  -- user id only means anything within one.
  UNIQUE KEY uk_user_app_grant_scope
    (avernet_tenant, app_id, user_id, env) GLOBAL,
  -- the user's view: which applications may act as me. Names no app, so the
  -- unique key cannot serve it past the tenant.
  KEY idx_user_app_grant_user
    (avernet_tenant, user_id, env) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'live user-level app delegations; a row exists iff the delegation is in force';

CREATE TABLE ac_user_app_grant_log (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  app_id BIGINT(20) UNSIGNED NOT NULL,
  app_name VARCHAR(1024) NOT NULL,
  user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL,
  action VARCHAR(32) NOT NULL COMMENT 'granted | revoked',
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  -- Deliberately NO unique key: this table accepts every event.
  KEY idx_user_app_grant_log_user
    (avernet_tenant, user_id, env, gmt_create) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'append-only history of user-level app delegation grants and revocations';
