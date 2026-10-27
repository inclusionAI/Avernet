DROP INDEX IF EXISTS idx_sessions_callback_status;
DROP INDEX IF EXISTS idx_session_callback_recovery;
ALTER TABLE bcs_groups DROP COLUMN service_spec;
ALTER TABLE bcs_group_sessions DROP COLUMN callback_status;
ALTER TABLE bcs_group_sessions DROP COLUMN callback_lease_owner;
ALTER TABLE bcs_group_sessions DROP COLUMN callback_lease_token;
ALTER TABLE bcs_group_sessions DROP COLUMN callback_lease_until_ms;
