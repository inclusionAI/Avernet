-- Add the delegating user to bot→app authorizations.
--
-- The record shipped meaning "this bot's OWNER authorized this app". That is
-- too narrow for how the platform is actually used: a person routinely works on
-- bots they do not own, as a member-level collaborator, and an integration
-- onboarded by such a person could reach nothing. The record now means
-- "app A may act as user U on bot B, which O owns" -- so it needs both people,
-- and they are the same person only when the delegator owns the bot.
--
-- SAFE ON AN EMPTY OR A POPULATED TABLE. The tables were reported empty, but
-- this does not rely on that: add nullable, backfill, then enforce NOT NULL.
--
-- Relying on it would have been a bad trade. Adding a NOT NULL column with no
-- default to a table that turned out to have rows fails loudly only in strict
-- mode; a permissive server fills the column with empty strings instead, and
-- the rekey below then makes every pre-existing grant unfindable while its
-- history loses the delegator -- silently, which is the outcome an assumption
-- like that exists to avoid.
--
-- The backfill is user_id = owner_id, which is right for every row the previous
-- revision could produce: only a bot's owner could grant, so the delegating
-- user and the owner were the same person by construction.
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
-- COMMENT belongs to the column definition and must therefore precede the
-- AFTER placement clause: the grammar is
--   ADD [COLUMN] col_name column_definition [FIRST | AFTER col_name]
-- and nothing may follow the placement clause. Written the other way round this
-- is a 1064 on a strict parser -- aborting the migration before the rekeying
-- below, leaving a table with no user_id under code that writes one -- or, on a
-- lenient one, silently reassigns the TABLE's comment.
ALTER TABLE ac_bot_app_grant
  ADD COLUMN user_id VARCHAR(256) COLLATE utf8mb4_bin NULL
    COMMENT 'delegating user, resolved server-side' AFTER bot_id;

UPDATE ac_bot_app_grant SET user_id = owner_id WHERE user_id IS NULL;

ALTER TABLE ac_bot_app_grant
  MODIFY COLUMN user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL
    COMMENT 'delegating user, resolved server-side';

-- Rekey uniqueness onto the delegating user. Runs after the backfill above, so
-- the new key is built over real values rather than over blanks.
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
  ADD COLUMN user_id VARCHAR(256) COLLATE utf8mb4_bin NULL AFTER bot_id;

UPDATE ac_bot_app_grant_log SET user_id = owner_id WHERE user_id IS NULL;

ALTER TABLE ac_bot_app_grant_log
  MODIFY COLUMN user_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL;
