-- Add the delegating user to bot→app authorizations.
--
-- The record shipped meaning "this bot's OWNER authorized this app". That is
-- too narrow for how the platform is actually used: a person routinely works on
-- bots they do not own, as a member-level collaborator, and an integration
-- onboarded by such a person could reach nothing. The record now means
-- "app A may act as user U on bot B, which O owns" -- so it needs both people,
-- and they are the same person only when the delegator owns the bot.
--
-- APPLY BEFORE THE FIRST GRANT IS WRITTEN. Both tables are deployed and empty,
-- which is the only reason this is a pure schema change: user_id is NOT NULL
-- with no default and nothing to backfill. Once a row exists, this file is
-- wrong -- the ALTER would be rejected -- and the correct migration adds
--
--     UPDATE ac_bot_app_grant SET user_id = owner_id WHERE user_id = '';
--
-- which is right for every row the previous revision could produce, since only
-- owners could grant. Check before applying rather than assuming.
--
-- Fresh installs get this shape directly from 2026_08_10_bot_app_grant.sql;
-- the two files must describe the same table column for column and index for
-- index, and the drift between them is the failure this directory exists to
-- prevent.

-- ---------------------------------------------------------------------------
-- Live table
-- ---------------------------------------------------------------------------

-- COLLATE is pinned explicitly rather than inherited. This is the column every
-- app-only request resolves on, so it has to compare byte-exact wherever it
-- runs; the deployed table carries utf8mb4_bin where the checked-in CREATE does
-- not, and an unqualified ADD COLUMN would silently take a different collation
-- in each place.
ALTER TABLE ac_bot_app_grant
  ADD COLUMN user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL AFTER bot_id
    COMMENT 'delegating user, resolved server-side';

-- Rekey uniqueness onto the delegating user.
--
-- user_id REPLACES owner_id in the key rather than joining it, for two
-- independent reasons.
--
-- Semantics: uniqueness is per delegation, and a delegation belongs to whoever
-- makes it. Two collaborators may each authorize the same app for the same bot;
-- those are two loans of two different authorities, independently withdrawable.
-- Keyed on the owner they collide, and the idempotent grant path swallows the
-- second as "already live" -- handing the second user an application bounded by
-- the first user's access.
--
-- Budget: the key is 2392 bytes with one user column (tenant 256 + app_id 8 +
-- bot_id 1024 + user 1024 + env 80). Carrying both would be 3416, past InnoDB's
-- 3072-byte cap -- the same wall that holds these columns at 256 characters.
ALTER TABLE ac_bot_app_grant
  DROP INDEX uk_bot_app_grant_scope;
ALTER TABLE ac_bot_app_grant
  ADD UNIQUE KEY uk_bot_app_grant_scope
    (avernet_tenant, app_id, bot_id, user_id, env) GLOBAL;

-- The app's view moves with it: "which bots may this app reach as this user".
ALTER TABLE ac_bot_app_grant
  DROP INDEX idx_bot_app_grant_app_owner;
ALTER TABLE ac_bot_app_grant
  ADD KEY idx_bot_app_grant_app_user
    (avernet_tenant, app_id, user_id, env) GLOBAL;

-- idx_bot_app_grant_bot_owner is deliberately left alone. Its
-- (avernet_tenant, bot_id) prefix already serves both reads that name no
-- delegating user -- the owner's listing of every grant on their bot, whoever
-- delegated it, and the sweep that revokes them all when the bot is deleted.

-- ---------------------------------------------------------------------------
-- History
-- ---------------------------------------------------------------------------

-- "Who let this application in, and when" has to survive the live row, which is
-- precisely when this table is read. No unique key here by design, so nothing
-- is constrained by the addition.
ALTER TABLE ac_bot_app_grant_log
  ADD COLUMN user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL AFTER bot_id;
