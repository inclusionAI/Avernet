-- Auth-session versioned columns on bcs_user_identities (Task 4, V1 auth-session
-- vertical slice). Adds the strict CAS columns used by the SQL-backed
-- `AuthSessionRepoPort::DbUserIdentityStore` implementation:
--
--   session_id          TEXT              nullable; NULL before the first
--                                       install and after revoke.
--   session_revision    INTEGER NOT NULL DEFAULT 0
--                                       CAS counter; 0 = no session installed
--                                       yet (contract's missing-install
--                                       state — `read_session_revision`
--                                       yields 0).
--   session_expires_at  INTEGER NOT NULL DEFAULT 0
--                                       Unix seconds of token expiry. 0
--                                       means "no active session": the
--                                       `session_expires_at > now` predicate
--                                       used by `get_session_by_hash` and
--                                       `rotate_session` is naturally false for
--                                       any positive `now`, so a never-
--                                       installed or revoked row never
--                                       matches.
--
-- Legacy `token`/`token_expire_at` rows are invalidated by the upgrade
-- (`token` is cleared to NULL, `token_expire_at` cleared to NULL); identities
-- and avatar are NOT deleted. The clear is guarded by `session_id IS NULL`
-- so a session installed via the new port (which always sets `session_id`)
-- survives a re-application of the body's UPDATE — the migration runner is
-- additionally a no-op on second run via `bcs_schema_migrations`.
--
-- SQLite 3.26 (rusqlite without bundled, this machine's system SQLite) has
-- no DROP COLUMN support: ALTER TABLE ADD COLUMN with CONSTANT defaults is
-- the only row-preserving schema change. All three additions use CONSTANT
-- defaults (`NULL` / `0` / `0`) and never touch existing rows.

ALTER TABLE bcs_user_identities ADD COLUMN session_id TEXT;
ALTER TABLE bcs_user_identities ADD COLUMN session_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bcs_user_identities ADD COLUMN session_expires_at INTEGER NOT NULL DEFAULT 0;
UPDATE bcs_user_identities
   SET token = NULL, token_expire_at = NULL
 WHERE session_id IS NULL
   AND (token IS NOT NULL OR token_expire_at IS NOT NULL);
