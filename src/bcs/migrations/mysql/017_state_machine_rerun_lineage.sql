-- Add State Machine Run lineage. A non-NULL rerun_of identifies the sole
-- direct child of a source Run and is the natural idempotency key for rerun.
ALTER TABLE `bcs_state_machine_runs`
  ADD COLUMN IF NOT EXISTS `root_run_id` varchar(128) DEFAULT NULL AFTER `run_id`,
  ADD COLUMN IF NOT EXISTS `rerun_of` varchar(128) DEFAULT NULL AFTER `root_run_id`,
  ADD COLUMN IF NOT EXISTS `session_activation_count` int(11) DEFAULT NULL AFTER `session_id`;

UPDATE `bcs_state_machine_runs`
SET `root_run_id` = `run_id`
WHERE `root_run_id` IS NULL;

ALTER TABLE `bcs_state_machine_runs`
  ADD UNIQUE INDEX `uk_sm_run_rerun_of` (`env`, `rerun_of`),
  ADD INDEX `idx_sm_runs_root` (`env`, `root_run_id`, `created_at_ms`);
