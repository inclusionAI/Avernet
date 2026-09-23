-- Auth-session versioned columns on bcs_user_identities (Task 4, V1 auth-session
-- vertical slice). Mirrors SQLite migration 028 with MySQL/OceanBase syntax.
--
--   session_id          VARCHAR(64)             nullable; NULL before the
--                                                 first install and after
--                                                 revoke.
--   session_revision    BIGINT NOT NULL DEFAULT 0
--                                                 CAS counter; 0 = no session
--                                                 installed yet (contract's
--                                                 missing-install state).
--   session_expires_at  BIGINT NOT NULL DEFAULT 0
--                                                 Unix seconds of token
--                                                 expiry; 0 = no active
--                                                 session (the
--                                                 `session_expires_at > now`
--                                                 predicate is naturally false).
--
-- Legacy `token`/`token_expire_at` rows are invalidated by the upgrade;
-- identities and avatar are NOT deleted. The clear is guarded by
-- `session_id IS NULL` so a session installed via the new port (which
-- always sets `session_id`) survives re-application of the UPDATE — the
-- migration runner is additionally a no-op on second run via
-- `bcs_schema_migrations`.
--
-- Live MySQL testing is deferred to Task 13 (the plan mandates a real MySQL
-- double-connection race harness, not a SQLite substitute), so this file is
-- applied by `bcs-admin db migrate --dialect mysql --apply` (or DBA-managed
-- deployment) without an in-repository live test enforcing parity here.

ALTER TABLE `bcs_user_identities`
    ADD COLUMN `session_id` VARCHAR(64) NULL,
    ADD COLUMN `session_revision` BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN `session_expires_at` BIGINT NOT NULL DEFAULT 0;
UPDATE `bcs_user_identities`
   SET `token` = NULL, `token_expire_at` = NULL
 WHERE `session_id` IS NULL
   AND (`token` IS NOT NULL OR `token_expire_at` IS NOT NULL);
