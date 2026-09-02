-- User-granted account-level user→app authorizations.
--
-- Two tables, for the reason 2026_08_10_bot_app_grant.sql gives: the live
-- table answers "may this app act as this user right now" — one answer, so it
-- carries a unique key the database enforces. The log answers "when could it,
-- historically" — unboundedly many answers, so it carries no unique key at
-- all. MySQL/OceanBase have no partial index to express "unique among the
-- live rows", so one soft-deleted table cannot hold both.
--
-- New tables only; no ALTER, so this applies to an existing database without
-- touching any current row. The ORM models in ../models.py must describe the
-- same tables column for column and index for index.

CREATE TABLE ac_user_app_grant (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  app_id BIGINT(20) UNSIGNED NOT NULL
    COMMENT 'gateway avernet_application.id, from the App principal',
  -- 1024: the gateway's app_name is unconstrained at its own boundary. Free to
  -- widen because app_name is in no index here.
  app_name VARCHAR(1024) NOT NULL
    COMMENT 'app display name, snapshotted at consent time',
  -- The authorizing user: a row means "this app may act as this user at the
  -- account level". COLLATE is pinned because every app-only request resolves
  -- on this column and it must compare byte-exact in every environment.
  user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL
    COMMENT 'authorizing user, resolved server-side',
  env VARCHAR(20) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  -- avernet_tenant leads the key: a user id only means anything within a
  -- tenant. env is in it so one authorization cannot collide across
  -- environments sharing a database.
  UNIQUE KEY uk_user_app_grant_scope
    (avernet_tenant, app_id, user_id, env) GLOBAL,
  -- the user's view: which applications may act as this user. The unique key
  -- reaches app_id before user_id, so it cannot serve a lookup naming no app.
  KEY idx_user_app_grant_user
    (avernet_tenant, user_id, env) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'live account-level user→app authorizations; a row exists iff access is in force';

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
  -- Deliberately NO unique key: this table's job is to accept every event,
  -- including the fourth revocation of the same pair.
  KEY idx_user_app_grant_log_user
    (avernet_tenant, user_id, env, gmt_create) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'append-only history of account-level user→app authorization grants and revocations';
