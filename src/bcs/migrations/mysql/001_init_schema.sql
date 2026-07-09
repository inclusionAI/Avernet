-- BCS MySQL/OceanBase baseline schema for the open-source v1 release.
-- Generated from the sanitized online BCS schema dump.
-- This file intentionally excludes runtime data, environment-specific database names,
-- OceanBase physical placement options, current AUTO_INCREMENT values, and non-business
-- repro/legacy tables.

-- Table: bcs_schema_migrations
CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
  `version` int(11) NOT NULL,
  `name` varchar(191) NOT NULL,
  `dialect` varchar(32) NOT NULL,
  `checksum` varchar(64) NOT NULL,
  `applied_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`version`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_actor_relations
CREATE TABLE IF NOT EXISTS `bcs_actor_relations` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `from_id` varchar(256) NOT NULL,
  `to_id` varchar(256) NOT NULL,
  `env` varchar(32) NOT NULL,
  `kinds` bigint(20) unsigned NOT NULL DEFAULT '0',
  `allow` bigint(20) unsigned NOT NULL DEFAULT '0',
  `deny` bigint(20) unsigned NOT NULL DEFAULT '0',
  `is_creator` tinyint(4) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_from_to_env` (`from_id`, `to_id`, `env`),
  KEY `idx_to_env` (`to_id`, `env`),
  KEY `idx_from_env_creator` (`from_id`, `env`, `is_creator`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_bots
CREATE TABLE IF NOT EXISTS `bcs_bots` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `bot_uuid` varchar(256) NOT NULL,
  `name` varchar(1024) NOT NULL,
  `bot_info` mediumtext DEFAULT NULL,
  `session_token` varchar(512) DEFAULT NULL,
  `registered_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(64) DEFAULT NULL,
  `visibility` varchar(32) NOT NULL DEFAULT 'public',
  `created_by` varchar(512) DEFAULT NULL,
  `actor_kind` varchar(16) NOT NULL DEFAULT 'bot',
  `status` varchar(16) NOT NULL DEFAULT 'online',
  `is_deleted` tinyint(4) NOT NULL DEFAULT '0',
  `agent_code` varchar(256) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_token` (`session_token`),
  UNIQUE KEY `uk_bot_env` (`bot_uuid`, `env`),
  KEY `idx_bcs_bots_actor_kind` (`actor_kind`),
  KEY `idx_agent_code` (`agent_code`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_collaboration_definitions
CREATE TABLE IF NOT EXISTS `bcs_collaboration_definitions` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `definition_id` varchar(128) NOT NULL,
  `version` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  `description` text DEFAULT NULL,
  `source_format` varchar(16) NOT NULL DEFAULT 'yaml',
  `content_hash` char(64) NOT NULL,
  `blob_id` varchar(128) DEFAULT NULL,
  `yaml_text` mediumtext DEFAULT NULL,
  `normalized_json` json DEFAULT NULL,
  `metadata_json` json DEFAULT NULL,
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  `created_by` varchar(128) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_definition_version` (`env`, `definition_id`, `version`),
  KEY `idx_definition_hash` (`env`, `content_hash`),
  KEY `idx_definition_blob` (`env`, `blob_id`),
  KEY `idx_definition_status` (`env`, `record_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_collaboration_definition_blobs
CREATE TABLE IF NOT EXISTS `bcs_collaboration_definition_blobs` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `blob_id` varchar(128) NOT NULL,
  `content_hash` char(64) NOT NULL,
  `content_encoding` varchar(32) NOT NULL DEFAULT 'identity',
  `content_size` bigint(20) unsigned NOT NULL,
  `content` mediumblob DEFAULT NULL,
  `external_uri` varchar(1024) DEFAULT NULL,
  `created_by` varchar(128) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_blob_id` (`env`, `blob_id`),
  KEY `idx_content_hash` (`env`, `content_hash`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_collaboration_events
CREATE TABLE IF NOT EXISTS `bcs_collaboration_events` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `state_machine_run_id` varchar(128) NOT NULL,
  `node_id` varchar(128) DEFAULT NULL,
  `attempt` int(11) DEFAULT NULL,
  `event_type` varchar(64) NOT NULL,
  `payload_json` json DEFAULT NULL,
  `created_at_ms` bigint(20) unsigned NOT NULL,
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (`id`),
  KEY `idx_collab_events_run` (`env`, `state_machine_run_id`, `id`),
  KEY `idx_collab_events_run_node` (`env`, `state_machine_run_id`, `node_id`, `attempt`, `id`),
  KEY `idx_collab_events_type_time` (`env`, `event_type`, `created_at_ms`),
  KEY `idx_collab_events_record_status` (`env`, `record_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_collaboration_templates
CREATE TABLE IF NOT EXISTS `bcs_collaboration_templates` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `template_id` varchar(128) NOT NULL,
  `source_type` varchar(16) NOT NULL DEFAULT 'system',
  `visibility` varchar(16) NOT NULL DEFAULT 'public',
  `owner_user_id` varchar(32) DEFAULT NULL,
  `priority` bigint(20) unsigned NOT NULL DEFAULT '4294967295',
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  `created_by` varchar(128) DEFAULT NULL,
  `updated_by` varchar(128) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bct_template` (`env`, `template_id`),
  KEY `idx_bct_env_status_priority` (`env`, `record_status`, `priority`, `template_id`),
  KEY `idx_bct_env_visibility_priority` (`env`, `visibility`, `record_status`, `priority`, `template_id`),
  KEY `idx_bct_env_owner_status` (`env`, `owner_user_id`, `record_status`, `priority`, `template_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_collaboration_template_contents
CREATE TABLE IF NOT EXISTS `bcs_collaboration_template_contents` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `template_id` varchar(128) NOT NULL,
  `lang` varchar(64) NOT NULL,
  `name` varchar(256) NOT NULL,
  `description` text DEFAULT NULL,
  `participant_summary_json` mediumtext NOT NULL,
  `definition_json` mediumtext NOT NULL,
  `yaml_text` mediumtext NOT NULL,
  `yaml_sha256` char(64) NOT NULL,
  `version` bigint(20) unsigned NOT NULL DEFAULT '1',
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bctc_template_lang` (`env`, `template_id`, `lang`),
  KEY `idx_bctc_env_lang_status` (`env`, `lang`, `record_status`, `template_id`),
  KEY `idx_bctc_env_hash` (`env`, `yaml_sha256`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_collaboration_template_tags
CREATE TABLE IF NOT EXISTS `bcs_collaboration_template_tags` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `template_id` varchar(128) NOT NULL,
  `tag` varchar(64) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bctt_template_tag` (`env`, `template_id`, `tag`),
  KEY `idx_bctt_env_tag` (`env`, `tag`, `template_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_friendships
CREATE TABLE IF NOT EXISTS `bcs_friendships` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `left_bot` varchar(255) NOT NULL,
  `right_bot` varchar(255) NOT NULL,
  `env` varchar(32) NOT NULL DEFAULT 'dev',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_pair` (`left_bot`, `right_bot`),
  KEY `idx_left_bot` (`left_bot`),
  KEY `idx_right_bot` (`right_bot`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_friend_requests
CREATE TABLE IF NOT EXISTS `bcs_friend_requests` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `request_id` varchar(64) NOT NULL,
  `from_bot` varchar(255) NOT NULL,
  `to_bot` varchar(255) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'pending',
  `env` varchar(32) NOT NULL DEFAULT 'dev',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_request_id` (`request_id`),
  KEY `idx_from_bot_status` (`from_bot`, `status`),
  KEY `idx_to_bot_status` (`to_bot`, `status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_groups
CREATE TABLE IF NOT EXISTS `bcs_groups` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `group_id` varchar(64) NOT NULL,
  `label` varchar(4096) DEFAULT NULL,
  `status` varchar(64) NOT NULL,
  `driver_bot` varchar(512) NOT NULL,
  `originator` varchar(512) DEFAULT NULL,
  `env` varchar(64) NOT NULL,
  `routing_policy_json` text DEFAULT NULL,
  `context` text DEFAULT NULL,
  `group_kind` varchar(16) NOT NULL DEFAULT 'normal',
  `dm_pair_key` varchar(513) DEFAULT NULL,
  `service_group_uuid` varchar(256) DEFAULT NULL,
  `service_mode` varchar(128) DEFAULT NULL,
  `version` int(11) NOT NULL DEFAULT '1',
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'active',
  `group_strategy` varchar(32) NOT NULL DEFAULT 'chat',
  `participants` text DEFAULT NULL,
  `service_spec` text DEFAULT NULL,
  `created_by` varchar(128) DEFAULT NULL,
  `visibility` varchar(32) NOT NULL DEFAULT 'private',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_group_env` (`group_id`, `env`),
  UNIQUE KEY `uk_dm_pair` (`env`, `dm_pair_key`),
  KEY `idx_driver` (`driver_bot`),
  KEY `idx_service_group_uuid` (`service_group_uuid`),
  KEY `idx_label` (`label`(191)),
  KEY `idx_visibility` (`visibility`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_group_participants
CREATE TABLE IF NOT EXISTS `bcs_group_participants` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `group_id` varchar(64) NOT NULL,
  `bot_uuid` varchar(256) NOT NULL,
  `role` varchar(256) NOT NULL,
  `env` varchar(64) NOT NULL,
  `actor_kind` varchar(16) NOT NULL DEFAULT 'bot',
  `mode` varchar(16) NOT NULL DEFAULT 'auto',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_group_bot` (`env`, `group_id`, `bot_uuid`),
  KEY `idx_bot` (`bot_uuid`),
  KEY `idx_group` (`group_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_group_runtime_bindings
CREATE TABLE IF NOT EXISTS `bcs_group_runtime_bindings` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `group_version` int(11) NOT NULL,
  `next_group_version` int(11) NOT NULL DEFAULT '2147483647',
  `default_definition_id` varchar(128) DEFAULT NULL,
  `default_definition_version` int(11) DEFAULT NULL,
  `definition_content_hash` char(64) DEFAULT NULL,
  `definition_blob_id` varchar(128) DEFAULT NULL,
  `auto_start_on_service_invocation` tinyint(4) NOT NULL DEFAULT '0',
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  `updated_by` varchar(128) DEFAULT NULL,
  `participant_bindings_json` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_group_binding_version` (`env`, `group_id`, `group_version`),
  KEY `idx_group_binding_current` (`env`, `group_id`, `record_status`, `next_group_version`),
  KEY `idx_group_binding_effective` (`env`, `group_id`, `record_status`, `group_version`, `next_group_version`),
  KEY `idx_group_binding_definition` (`env`, `default_definition_id`, `default_definition_version`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_group_sessions
CREATE TABLE IF NOT EXISTS `bcs_group_sessions` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `session_id` varchar(64) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `env` varchar(32) NOT NULL DEFAULT 'prod',
  `status` varchar(16) NOT NULL DEFAULT 'running',
  `session_kind` varchar(32) NOT NULL DEFAULT 'chat',
  `session_title` varchar(256) DEFAULT NULL,
  `group_version` int(11) DEFAULT NULL,
  `caller_id` varchar(256) DEFAULT NULL,
  `input` text DEFAULT NULL,
  `output` text DEFAULT NULL,
  `error_message` text DEFAULT NULL,
  `callback_status` varchar(32) DEFAULT NULL,
  `activation_count` int(11) NOT NULL DEFAULT '1',
  `caller_principal` varchar(128) DEFAULT NULL,
  `created_by` varchar(128) DEFAULT NULL,
  `participants` text NOT NULL,
  `completed_at` bigint(20) unsigned DEFAULT NULL,
  `meta` text DEFAULT NULL,
  `current_msg_seq` bigint(20) NOT NULL DEFAULT '0',
  `participant_join_seq` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_id` (`env`, `session_id`),
  KEY `idx_group_status` (`env`, `group_id`, `status`),
  KEY `idx_group_kind_status` (`env`, `group_id`, `session_kind`, `status`),
  KEY `idx_caller_principal` (`env`, `caller_principal`),
  KEY `idx_callback_status` (`env`, `callback_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_identity_links
CREATE TABLE IF NOT EXISTS `bcs_identity_links` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `internal_id` varchar(256) NOT NULL,
  `auth_source` varchar(64) NOT NULL,
  `external_id` varchar(256) NOT NULL,
  `external_owner_id` varchar(128) DEFAULT NULL,
  `provider_id` varchar(128) DEFAULT NULL,
  `actor_kind` varchar(16) NOT NULL,
  `env` varchar(64) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_identity` (`auth_source`, `external_id`, `external_owner_id`, `provider_id`, `env`),
  KEY `idx_internal` (`internal_id`, `env`),
  KEY `idx_external` (`external_id`, `env`),
  KEY `idx_provider` (`provider_id`, `external_id`, `env`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_messages
CREATE TABLE IF NOT EXISTS `bcs_messages` (
  `message_id` varchar(64) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `session_id` varchar(128) NOT NULL,
  `session_seq` bigint(20) NOT NULL,
  `env` varchar(32) NOT NULL,
  `sender_id` varchar(256) NOT NULL,
  `sender_type` varchar(32) NOT NULL,
  `message_type` varchar(64) NOT NULL,
  `content` mediumtext NOT NULL,
  `client_msg_id` varchar(256) DEFAULT NULL,
  `status` varchar(32) DEFAULT 'normal',
  `created_at` bigint(20) NOT NULL,
  `ttl_until` bigint(20) DEFAULT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `run_id` varchar(256) NOT NULL DEFAULT '',
  PRIMARY KEY (`message_id`),
  UNIQUE KEY `uk_session_seq` (`session_id`, `session_seq`),
  KEY `idx_group_created` (`group_id`, `created_at`),
  KEY `idx_group_session` (`group_id`, `session_id`),
  KEY `idx_session_created` (`session_id`, `created_at`),
  KEY `idx_session_sender_created` (`session_id`, `sender_id`, `created_at`),
  KEY `idx_session_type_created` (`session_id`, `message_type`, `created_at`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_providers
CREATE TABLE IF NOT EXISTS `bcs_providers` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `provider_id` varchar(256) NOT NULL,
  `env` varchar(64) NOT NULL,
  `name` varchar(1024) NOT NULL,
  `config` mediumtext NOT NULL,
  `disabled` tinyint(11) NOT NULL DEFAULT '0',
  `created_by` varchar(256) NOT NULL,
  `owners` text NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_provider_env` (`env`, `provider_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_provider_bot_bindings
CREATE TABLE IF NOT EXISTS `bcs_provider_bot_bindings` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `bot_uuid` varchar(256) NOT NULL,
  `provider_id` varchar(256) NOT NULL,
  `provider_bot_ref` varchar(256) NOT NULL,
  `env` varchar(64) NOT NULL,
  `disabled` tinyint(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_provider_ref_env` (`env`, `provider_id`, `provider_bot_ref`),
  UNIQUE KEY `uk_bot_uuid_env` (`env`, `bot_uuid`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_channel_bindings
CREATE TABLE IF NOT EXISTS `bcs_channel_bindings` (
  `id` varchar(64) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `channel_type` varchar(32) NOT NULL,
  `account_ref` varchar(128) NOT NULL,
  `target_json` text NOT NULL,
  `group_chat_scope` varchar(32) DEFAULT NULL,
  `visibility` varchar(32) NOT NULL,
  `env` varchar(32) NOT NULL,
  `status` varchar(16) NOT NULL,
  `created_by` varchar(256) DEFAULT NULL,
  `config_json` text NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_channel_bindings_account` (`channel_type`, `account_ref`, `status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_channel_conversations
CREATE TABLE IF NOT EXISTS `bcs_channel_conversations` (
  `binding_id` varchar(64) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `im_conversation_id` varchar(256) NOT NULL,
  `im_conversation_type` varchar(16) NOT NULL,
  `session_scope` varchar(32) NOT NULL,
  `im_user_id` varchar(128) NOT NULL DEFAULT '',
  `bcs_session_id` varchar(128) NOT NULL,
  `last_active_at` bigint(20) unsigned NOT NULL,
  PRIMARY KEY (`binding_id`, `im_conversation_id`, `session_scope`, `im_user_id`),
  KEY `idx_channel_conversations_session` (`binding_id`, `bcs_session_id`),
  KEY `idx_channel_conversations_bcs_session` (`bcs_session_id`, `binding_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_channel_im_participants
CREATE TABLE IF NOT EXISTS `bcs_channel_im_participants` (
  `channel_type` varchar(32) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `account_ref` varchar(128) NOT NULL,
  `im_user_id` varchar(128) NOT NULL,
  `actor_id` varchar(256) NOT NULL,
  `display_name` varchar(256) DEFAULT NULL,
  PRIMARY KEY (`channel_type`, `account_ref`, `im_user_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_provider_credentials
CREATE TABLE IF NOT EXISTS `bcs_provider_credentials` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `provider_id` varchar(256) NOT NULL,
  `env` varchar(64) NOT NULL,
  `credential_kind` varchar(128) NOT NULL,
  `secret_value` varchar(512) NOT NULL,
  `disabled` tinyint(4) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_provider_credential_kind` (`env`, `provider_id`, `credential_kind`),
  KEY `idx_credential_lookup` (`env`, `credential_kind`, `secret_value`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_service_group_instances
CREATE TABLE IF NOT EXISTS `bcs_service_group_instances` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `group_id` varchar(64) NOT NULL,
  `service_group_uuid` varchar(256) NOT NULL,
  `service_group_version` int(11) NOT NULL,
  `instance_meta` mediumtext DEFAULT NULL,
  `callback_status` varchar(128) DEFAULT NULL,
  `reactivation_log` mediumtext DEFAULT NULL,
  `instance_result` mediumtext DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_group_id` (`group_id`),
  KEY `idx_service_group_uuid` (`service_group_uuid`),
  KEY `idx_callback_status` (`callback_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_service_group_templates
CREATE TABLE IF NOT EXISTS `bcs_service_group_templates` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `uuid` varchar(256) NOT NULL,
  `version` int(11) NOT NULL DEFAULT '1',
  `publish_status` varchar(128) NOT NULL DEFAULT 'draft',
  `name` varchar(2048) NOT NULL,
  `description` text DEFAULT NULL,
  `participants` mediumtext NOT NULL,
  `service_mode` varchar(128) NOT NULL,
  `mode_config` mediumtext DEFAULT NULL,
  `callback_config` text DEFAULT NULL,
  `max_concurrency` int(11) NOT NULL DEFAULT '-1',
  `created_by` varchar(256) NOT NULL,
  `modified_by` varchar(256) NOT NULL,
  `env` varchar(128) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_uuid_version` (`uuid`, `version`),
  KEY `idx_uuid` (`uuid`),
  KEY `idx_created_by` (`created_by`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_session_participants
CREATE TABLE IF NOT EXISTS `bcs_session_participants` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `session_id` varchar(64) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `bot_uuid` varchar(256) NOT NULL,
  `role` varchar(64) NOT NULL,
  `env` varchar(32) NOT NULL DEFAULT 'prod',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_session_bot` (`env`, `session_id`, `bot_uuid`),
  KEY `idx_bot` (`env`, `bot_uuid`),
  KEY `idx_session` (`env`, `session_id`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_state_machine_definition_snapshots
CREATE TABLE IF NOT EXISTS `bcs_state_machine_definition_snapshots` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `run_id` varchar(128) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `session_id` varchar(64) NOT NULL,
  `group_version` int(11) NOT NULL,
  `definition_id` varchar(128) NOT NULL,
  `definition_version` int(11) NOT NULL,
  `definition_content_hash` char(64) NOT NULL,
  `snapshot_blob_id` varchar(128) DEFAULT NULL,
  `snapshot_json` json DEFAULT NULL,
  `source_format` varchar(16) NOT NULL DEFAULT 'yaml',
  `resolved_participant_bindings_json` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_definition_snapshot_run` (`env`, `run_id`),
  KEY `idx_definition_snapshot_group_version` (`env`, `group_id`, `group_version`),
  KEY `idx_definition_snapshot_definition` (`env`, `definition_id`, `definition_version`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_state_machine_delivery_correlations
CREATE TABLE IF NOT EXISTS `bcs_state_machine_delivery_correlations` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `state_machine_run_id` varchar(128) NOT NULL,
  `node_id` varchar(128) NOT NULL,
  `attempt` int(11) NOT NULL,
  `assignee_bot_id` varchar(128) NOT NULL,
  `delivery_request_id` varchar(191) NOT NULL,
  `bot_delivery_run_id` varchar(191) DEFAULT NULL,
  `created_at_ms` bigint(20) unsigned NOT NULL,
  `updated_at_ms` bigint(20) unsigned NOT NULL,
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sm_corr_delivery_request` (`env`, `delivery_request_id`),
  UNIQUE KEY `uk_sm_corr_bot_delivery_run` (`env`, `bot_delivery_run_id`),
  KEY `idx_sm_corr_run_node_attempt` (`env`, `state_machine_run_id`, `node_id`, `attempt`),
  KEY `idx_sm_corr_assignee` (`env`, `assignee_bot_id`, `created_at_ms`),
  KEY `idx_sm_corr_record_status` (`env`, `record_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_state_machine_node_runs
CREATE TABLE IF NOT EXISTS `bcs_state_machine_node_runs` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `run_id` varchar(128) NOT NULL,
  `node_id` varchar(128) NOT NULL,
  `status` varchar(32) NOT NULL,
  `attempt` int(11) NOT NULL DEFAULT '0',
  `node_timeout_ms` bigint(20) unsigned DEFAULT NULL,
  `timeout_deadline_ms` bigint(20) unsigned DEFAULT NULL,
  `max_attempts` int(11) NOT NULL DEFAULT '1',
  `assignee_bot_id` varchar(128) NOT NULL,
  `delivery_request_id` varchar(191) DEFAULT NULL,
  `bot_delivery_run_id` varchar(191) DEFAULT NULL,
  `artifact_text` mediumtext DEFAULT NULL,
  `error_message` text DEFAULT NULL,
  `started_at_ms` bigint(20) unsigned DEFAULT NULL,
  `completed_at_ms` bigint(20) unsigned DEFAULT NULL,
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sm_node_run` (`env`, `run_id`, `node_id`),
  KEY `idx_sm_nodes_run_status` (`env`, `run_id`, `status`),
  KEY `idx_sm_nodes_status_started` (`env`, `status`, `started_at_ms`),
  KEY `idx_sm_nodes_timeout_deadline` (`env`, `status`, `timeout_deadline_ms`),
  KEY `idx_sm_nodes_assignee_status` (`env`, `assignee_bot_id`, `status`),
  KEY `idx_sm_nodes_delivery_request` (`env`, `delivery_request_id`),
  KEY `idx_sm_nodes_bot_delivery_run` (`env`, `bot_delivery_run_id`),
  KEY `idx_sm_nodes_record_status` (`env`, `record_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_state_machine_runs
CREATE TABLE IF NOT EXISTS `bcs_state_machine_runs` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `run_id` varchar(128) NOT NULL,
  `definition_id` varchar(128) NOT NULL,
  `definition_version` int(11) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `group_version` int(11) NOT NULL,
  `session_id` varchar(64) NOT NULL,
  `created_by` varchar(128) DEFAULT NULL,
  `status` varchar(32) NOT NULL,
  `input_json` json DEFAULT NULL,
  `output_text` mediumtext DEFAULT NULL,
  `error_message` text DEFAULT NULL,
  `created_at_ms` bigint(20) unsigned NOT NULL,
  `updated_at_ms` bigint(20) unsigned NOT NULL,
  `completed_at_ms` bigint(20) unsigned DEFAULT NULL,
  `record_status` varchar(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sm_run_id` (`env`, `run_id`),
  KEY `idx_sm_runs_session` (`env`, `session_id`),
  KEY `idx_sm_runs_created_by` (`env`, `created_by`, `created_at_ms`),
  KEY `idx_sm_runs_group_status` (`env`, `group_id`, `status`, `created_at_ms`),
  KEY `idx_sm_runs_status_updated` (`env`, `status`, `updated_at_ms`),
  KEY `idx_sm_runs_definition` (`env`, `definition_id`, `definition_version`),
  KEY `idx_sm_runs_record_status` (`env`, `record_status`)
) DEFAULT CHARSET = utf8mb4;

-- Table: bcs_user_identities
CREATE TABLE IF NOT EXISTS `bcs_user_identities` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `user_id` varchar(32) NOT NULL,
  `auth_source` varchar(64) NOT NULL,
  `external_user_id` varchar(512) NOT NULL,
  `user_name` varchar(256) DEFAULT NULL,
  `external_user_name` varchar(256) DEFAULT NULL,
  `avatar` varchar(1024) DEFAULT NULL,
  `token` varchar(64) DEFAULT NULL,
  `token_expire_at` timestamp NULL DEFAULT NULL,
  `env` varchar(64) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_id` (`user_id`),
  UNIQUE KEY `uk_external` (`auth_source`, `external_user_id`, `env`),
  KEY `idx_external` (`external_user_id`, `env`)
) DEFAULT CHARSET = utf8mb4;

-- Record the open-source v1 baseline after all schema objects are created.
INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
VALUES (1, 'init_schema', 'mysql', 'b3de64c97b982a735230f6c55e966e4404eb509d70a0b7fd8f11dfa43e3452a7');
