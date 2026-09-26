-- Human Participant message projection and producer-declared visibility.
-- Existing participants and sessions retain legacy/full behavior. Existing
-- messages remain unclassified; message_visibility_version is metadata for
-- migration/observability only and is not an authorization gate.
ALTER TABLE `bcs_group_participants`
  ADD COLUMN IF NOT EXISTS `message_view_scope` varchar(32) NOT NULL DEFAULT 'full'
  AFTER `tags_json`;

ALTER TABLE `bcs_group_sessions`
  ADD COLUMN IF NOT EXISTS `message_visibility_version` tinyint(4) NOT NULL DEFAULT '0'
  AFTER `session_kind`;

ALTER TABLE `bcs_messages`
  ADD COLUMN IF NOT EXISTS `visibility_domain` varchar(32) DEFAULT NULL
    AFTER `owner_bot_id`,
  ADD COLUMN IF NOT EXISTS `audience_kind` varchar(32) DEFAULT NULL
    AFTER `visibility_domain`,
  ADD COLUMN IF NOT EXISTS `audience_actor_ids_json` text DEFAULT NULL
    AFTER `audience_kind`,
  ADD INDEX `idx_messages_session_audience_created`
    (`session_id`, `visibility_domain`, `audience_kind`, `created_at`, `session_seq`);
