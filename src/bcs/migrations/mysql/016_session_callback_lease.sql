-- Add the minimal activation-aware callback claim state used by normal
-- dispatch and failover recovery. NULL token identifies pre-FO activations
-- that must not be scanned automatically. The recovery index puts the
-- equality predicates before the token so periodic scans can discard legacy
-- NULL-token rows before evaluating lease expiry.
ALTER TABLE `bcs_group_sessions`
  ADD COLUMN IF NOT EXISTS `callback_lease_owner` varchar(128) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `callback_lease_token` bigint DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `callback_lease_until_ms` bigint unsigned DEFAULT NULL,
  ADD INDEX `idx_session_callback_recovery`
    (`env`, `session_kind`, `status`, `callback_status`, `callback_lease_token`,
     `callback_lease_until_ms`, `session_id`);
