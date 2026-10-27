-- Apply only after every old BCS replica is stopped. Export legacy configuration
-- before cleanup if rollback to an old release may be needed.
ALTER TABLE `bcs_groups` DROP COLUMN `service_spec`;
ALTER TABLE `bcs_group_sessions`
  DROP INDEX `idx_callback_status`,
  DROP INDEX `idx_session_callback_recovery`,
  DROP COLUMN `callback_status`,
  DROP COLUMN `callback_lease_owner`,
  DROP COLUMN `callback_lease_token`,
  DROP COLUMN `callback_lease_until_ms`;
