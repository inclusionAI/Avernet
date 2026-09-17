ALTER TABLE `bcs_group_participants`
  ADD COLUMN `tags_json` text DEFAULT NULL AFTER `mode`;
