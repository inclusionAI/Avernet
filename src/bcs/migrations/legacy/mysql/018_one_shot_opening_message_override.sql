-- Persist only request-level opening-message overrides for one-shot
-- StateMachine Runs. Configured StateMachine Runs keep this column NULL and
-- continue to resolve their opening message from the Group.
ALTER TABLE `bcs_state_machine_runs`
  ADD COLUMN IF NOT EXISTS `opening_message_override_json` text DEFAULT NULL
  AFTER `input_json`;
