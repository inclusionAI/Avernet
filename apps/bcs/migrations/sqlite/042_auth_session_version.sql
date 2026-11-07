-- 042_auth_session_version: strict versioned login sessions for the V1 API
-- auth plugin chain. `bcs_user_identities` stays the single shared identity +
-- session row; the nullable `session_id`, non-null `session_revision`
-- (default 0), and the `session_expires_at` Unix-seconds column make session
-- expiry comparisons unambiguous across dialects. Session scope stays
-- (user_id, auth_source, env) on the existing unique index.
-- Historical opaque browser tokens are invalidated on purpose: the strict
-- chain resolves sessions only by token hash + revision under scope, never
-- by the legacy token, so the migration clears `token`/`token_expire_at`
-- exactly once while the version-42 record is absent.
ALTER TABLE bcs_user_identities ADD COLUMN session_id TEXT DEFAULT NULL;
ALTER TABLE bcs_user_identities ADD COLUMN session_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bcs_user_identities ADD COLUMN session_expires_at INTEGER NOT NULL DEFAULT 0;
UPDATE bcs_user_identities SET token = NULL, token_expire_at = NULL;
