-- Durable BCN final-callback delivery state (migration-first rollout).
-- Safe to re-run on MariaDB/OceanBase versions supporting IF NOT EXISTS.
-- Run before deploying callback-aware workers; do not rely on ORM create_all().

ALTER TABLE `baas_bot_run`
  ADD COLUMN IF NOT EXISTS `bcn_callback_function` varchar(64) DEFAULT NULL COMMENT 'BCN callback function discriminator',
  ADD COLUMN IF NOT EXISTS `bcn_callback_eligible` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'Whether durable BCN callback delivery applies',
  ADD COLUMN IF NOT EXISTS `bcn_callback_state` varchar(32) DEFAULT NULL COMMENT 'PENDING/IN_FLIGHT/SENT/TERMINAL_FAILED',
  ADD COLUMN IF NOT EXISTS `bcn_callback_sent` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'Durable sent invariant for BCN callback',
  ADD COLUMN IF NOT EXISTS `bcn_callback_attempts` int(11) NOT NULL DEFAULT '0' COMMENT 'Callback invocation count',
  ADD COLUMN IF NOT EXISTS `bcn_callback_next_retry_at` timestamp NULL DEFAULT NULL COMMENT 'Next callback recovery time',
  ADD COLUMN IF NOT EXISTS `bcn_callback_in_flight_until` timestamp NULL DEFAULT NULL COMMENT 'Callback lease expiry',
  ADD COLUMN IF NOT EXISTS `bcn_callback_lease_token` varchar(128) DEFAULT NULL COMMENT 'Fenced callback lease token',
  ADD COLUMN IF NOT EXISTS `bcn_callback_lease_generation` bigint(20) NOT NULL DEFAULT '0' COMMENT 'Fenced callback lease generation',
  ADD COLUMN IF NOT EXISTS `bcn_callback_last_status` varchar(32) DEFAULT NULL COMMENT 'Last HTTP status or transport category',
  ADD COLUMN IF NOT EXISTS `bcn_callback_last_error_kind` varchar(64) DEFAULT NULL COMMENT 'Last redacted callback error category';

ALTER TABLE `baas_bot_run`
  ADD KEY IF NOT EXISTS `idx_bcn_callback_due`
    (`bcn_callback_eligible`, `bcn_callback_state`, `bcn_callback_next_retry_at`, `status`);

-- The queue is consulted only for the legacy sent marker.  Eligibility is
-- constrained by the durable run metadata and the explicit legacy callback
-- discriminator so unrelated queue markers cannot be promoted.
UPDATE `baas_bot_run` r
JOIN `baas_bot_run_queue` q ON q.`run_id` = r.`run_id`
SET r.`bcn_callback_eligible` = 1,
    r.`bcn_callback_function` = 'bcn_uplink',
    r.`bcn_callback_state` = 'SENT',
    r.`bcn_callback_sent` = 1,
    r.`bcn_callback_next_retry_at` = NULL,
    r.`bcn_callback_in_flight_until` = NULL,
    r.`bcn_callback_lease_token` = NULL
WHERE r.`status` IN ('COMPLETED', 'FAILED', 'TIME_OUT', 'ABORTED')
  AND r.`bcn_callback_sent` = 0
  AND JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(r.`metadata`) THEN r.`metadata` ELSE '{}' END,
        '$.bot_options.from_bcn'
      )) = 'true'
  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(r.`metadata`) THEN r.`metadata` ELSE '{}' END,
        '$.request_type'
      )), '') <> 'inject'
  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(r.`metadata`) THEN r.`metadata` ELSE '{}' END,
        '$.stream'
      )), '') <> 'true'
  AND JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(q.`meta`) THEN q.`meta` ELSE '{}' END,
        '$.callback_function'
      )) = 'bcn_uplink'
  AND JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(q.`meta`) THEN q.`meta` ELSE '{}' END,
        '$.bcn_callback_sent'
      )) = 'true';

-- Initialize legacy eligible rows whose queue callback was not marked sent.
-- This intentionally does not join the queue: an eligible legacy row remains
-- recoverable if its transient queue row was purged before migration or the
-- run is still active during the rolling deployment window.
UPDATE `baas_bot_run` r
SET r.`bcn_callback_eligible` = 1,
    r.`bcn_callback_function` = 'bcn_uplink',
    r.`bcn_callback_state` = COALESCE(r.`bcn_callback_state`, 'PENDING'),
    r.`bcn_callback_attempts` = COALESCE(r.`bcn_callback_attempts`, 0)
WHERE r.`bcn_callback_sent` = 0
  AND JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(r.`metadata`) THEN r.`metadata` ELSE '{}' END,
        '$.bot_options.from_bcn'
      )) = 'true'
  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(r.`metadata`) THEN r.`metadata` ELSE '{}' END,
        '$.request_type'
      )), '') <> 'inject'
  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(r.`metadata`) THEN r.`metadata` ELSE '{}' END,
        '$.stream'
      )), '') <> 'true'
  AND COALESCE(r.`bcn_callback_function`, 'bcn_uplink') = 'bcn_uplink';
