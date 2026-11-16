-- 042_auth_session_version: strict versioned login sessions for the V1 API
-- auth plugin chain. Semantically identical to the SQLite 042 migration
-- modulo dialect: nullable `session_id`, non-null `session_revision`
-- (default 0), and the `session_expires_at` BIGINT Unix-seconds column.
-- Session scope stays (`user_id`, `auth_source`, `env`) on the existing
-- `uk_user_id` unique index. Historical opaque browser tokens are
-- invalidated on purpose: the strict chain resolves sessions only by token
-- hash + revision under scope, never by the legacy token, so the migration
-- clears `token`/`token_expire_at` exactly once, in the same apply that
-- bcs-admin records version 42.
ALTER TABLE `bcs_user_identities`
  ADD COLUMN `session_id` VARCHAR(64) DEFAULT NULL,
  ADD COLUMN `session_revision` BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN `session_expires_at` BIGINT NOT NULL DEFAULT 0;
UPDATE `bcs_user_identities` SET `token` = NULL, `token_expire_at` = NULL;
