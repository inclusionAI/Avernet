-- Expand canonical BCS session identifiers for source-identifying Channel ids.
-- The statements are intentionally static so identifiers cannot be influenced
-- by runtime input.
ALTER TABLE `bcs_group_sessions`
  MODIFY COLUMN `session_id` varchar(128) NOT NULL;

ALTER TABLE `bcs_session_participants`
  MODIFY COLUMN `session_id` varchar(128) NOT NULL;

ALTER TABLE `bcs_state_machine_definition_snapshots`
  MODIFY COLUMN `session_id` varchar(128) NOT NULL;

ALTER TABLE `bcs_state_machine_runs`
  MODIFY COLUMN `session_id` varchar(128) NOT NULL;

ALTER TABLE `bcs_session_files`
  MODIFY COLUMN `session_id` varchar(128) NOT NULL;
