-- Persist ownership of asynchronous state-machine judge work. The node status
-- itself is stored in the existing `status` column as `judging`.
ALTER TABLE `bcs_state_machine_node_runs`
  ADD COLUMN `judge_claim_token` varchar(64) DEFAULT NULL,
  ADD COLUMN `judge_lease_until_ms` bigint(20) unsigned DEFAULT NULL,
  ADD COLUMN `judge_decision_json` mediumtext DEFAULT NULL;

CREATE INDEX `idx_sm_nodes_judge_claim`
  ON `bcs_state_machine_node_runs` (`env`, `status`, `judge_lease_until_ms`);
