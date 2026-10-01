ALTER TABLE `bcs_group_participants`
  ADD COLUMN IF NOT EXISTS `tags_json` text DEFAULT NULL AFTER `mode`;
