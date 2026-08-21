CREATE TABLE IF NOT EXISTS `bcs_event_subscriptions` (
  `subscription_id` varchar(128) NOT NULL,
  `name` varchar(128) NOT NULL,
  `scope_type` varchar(32) NOT NULL,
  `scope_id` varchar(256) NOT NULL,
  `status` varchar(32) NOT NULL,
  `current_revision` bigint(20) unsigned NOT NULL,
  `created_by_type` varchar(32) NOT NULL,
  `created_by_id` varchar(256) NOT NULL,
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `deleted_at` timestamp(3) NULL DEFAULT NULL,
  `env` varchar(32) NOT NULL,
  PRIMARY KEY (`subscription_id`),
  KEY `idx_event_subscription_scope` (`env`, `scope_type`, `scope_id`, `status`),
  KEY `idx_event_subscription_status` (`env`, `status`, `updated_at`),
  KEY `idx_event_subscription_creator` (`env`, `created_by_type`, `created_by_id`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_subscription_revisions` (
  `subscription_id` varchar(128) NOT NULL,
  `revision` bigint(20) unsigned NOT NULL,
  `event_filters_json` text NOT NULL,
  `payload_mode` varchar(32) NOT NULL,
  `endpoint_url` varchar(2048) NOT NULL,
  `request_timeout_ms` bigint(20) unsigned NOT NULL,
  `activated_at` timestamp(3) NOT NULL,
  `retired_at` timestamp(3) NULL DEFAULT NULL,
  `env` varchar(32) NOT NULL,
  PRIMARY KEY (`subscription_id`, `revision`),
  KEY `idx_event_revision_active` (`env`, `subscription_id`, `retired_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_scope_epochs` (
  `env` varchar(32) NOT NULL,
  `scope_type` varchar(32) NOT NULL,
  `scope_id` varchar(256) NOT NULL,
  `epoch` bigint(20) unsigned NOT NULL DEFAULT '0',
  `updated_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`env`, `scope_type`, `scope_id`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_streams` (
  `env` varchar(32) NOT NULL,
  `stream_key` varchar(384) NOT NULL,
  `last_sequence` bigint(20) unsigned NOT NULL DEFAULT '0',
  `updated_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`env`, `stream_key`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_events` (
  `event_id` varchar(128) NOT NULL,
  `event_type` varchar(128) NOT NULL,
  `schema_version` varchar(32) NOT NULL,
  `producer` varchar(64) NOT NULL,
  `producer_key` varchar(256) NOT NULL,
  `subject_type` varchar(64) NOT NULL,
  `subject_id` varchar(256) NOT NULL,
  `group_id` varchar(256) DEFAULT NULL,
  `session_id` varchar(256) DEFAULT NULL,
  `task_id` varchar(256) DEFAULT NULL,
  `run_id` varchar(256) DEFAULT NULL,
  `stream_key` varchar(384) NOT NULL,
  `sequence` bigint(20) unsigned NOT NULL,
  `actor_json` text DEFAULT NULL,
  `correlation_id` varchar(256) DEFAULT NULL,
  `causation_event_id` varchar(128) DEFAULT NULL,
  `trace_id` varchar(256) DEFAULT NULL,
  `data_json` mediumtext NOT NULL,
  `occurred_at` timestamp(3) NOT NULL,
  `recorded_at` timestamp(3) NOT NULL,
  `fanout_status` varchar(32) NOT NULL,
  `retention_until` timestamp(3) NOT NULL,
  `env` varchar(32) NOT NULL,
  PRIMARY KEY (`event_id`),
  UNIQUE KEY `uk_event_producer` (`env`, `producer`, `producer_key`, `event_type`),
  UNIQUE KEY `uk_event_stream_sequence` (`env`, `stream_key`, `sequence`),
  KEY `idx_event_fanout_status` (`env`, `fanout_status`, `recorded_at`),
  KEY `idx_event_scope_type` (`env`, `group_id`(128), `session_id`(128), `event_type`, `recorded_at`),
  KEY `idx_event_causation` (`env`, `causation_event_id`),
  KEY `idx_event_retention` (`env`, `retention_until`, `fanout_status`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_fanout_targets` (
  `target_id` varchar(128) NOT NULL,
  `event_id` varchar(128) NOT NULL,
  `subscription_id` varchar(128) NOT NULL,
  `subscription_revision` bigint(20) unsigned NOT NULL,
  `purpose` varchar(32) NOT NULL,
  `replay_request_id` varchar(128) NOT NULL DEFAULT '',
  `replay_of_delivery_id` varchar(128) DEFAULT NULL,
  `depends_on_target_id` varchar(128) DEFAULT NULL,
  `status` varchar(32) NOT NULL,
  `lease_owner` varchar(256) DEFAULT NULL,
  `lease_until` timestamp(3) NULL DEFAULT NULL,
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `materialized_at` timestamp(3) NULL DEFAULT NULL,
  `cancelled_at` timestamp(3) NULL DEFAULT NULL,
  `env` varchar(32) NOT NULL,
  PRIMARY KEY (`target_id`),
  UNIQUE KEY `uk_event_target_idempotency` (`env`, `subscription_id`, `subscription_revision`, `event_id`, `purpose`, `replay_request_id`),
  KEY `idx_event_target_pending` (`env`, `status`, `lease_until`, `created_at`),
  KEY `idx_event_target_dependency` (`env`, `depends_on_target_id`, `status`),
  KEY `idx_event_target_subscription` (`env`, `subscription_id`, `subscription_revision`, `status`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_deliveries` (
  `delivery_id` varchar(128) NOT NULL,
  `fanout_target_id` varchar(128) NOT NULL,
  `event_id` varchar(128) NOT NULL,
  `subscription_id` varchar(128) NOT NULL,
  `subscription_revision` bigint(20) unsigned NOT NULL,
  `stream_key` varchar(384) NOT NULL,
  `sequence` bigint(20) unsigned NOT NULL,
  `payload_bytes` mediumblob NOT NULL,
  `payload_sha256` char(64) NOT NULL,
  `status` varchar(32) NOT NULL,
  `attempt_count` bigint(20) unsigned NOT NULL DEFAULT '0',
  `first_attempt_at` timestamp(3) NULL DEFAULT NULL,
  `last_attempt_at` timestamp(3) NULL DEFAULT NULL,
  `next_attempt_at` timestamp(3) NULL DEFAULT NULL,
  `lease_owner` varchar(256) DEFAULT NULL,
  `lease_until` timestamp(3) NULL DEFAULT NULL,
  `last_http_status` int DEFAULT NULL,
  `last_error_category` varchar(128) DEFAULT NULL,
  `last_error_summary` varchar(2048) DEFAULT NULL,
  `dead_lettered_at` timestamp(3) NULL DEFAULT NULL,
  `cancelled_at` timestamp(3) NULL DEFAULT NULL,
  `skipped_at` timestamp(3) NULL DEFAULT NULL,
  `skip_actor` text DEFAULT NULL,
  `skip_reason` varchar(128) DEFAULT NULL,
  `replay_of_delivery_id` varchar(128) DEFAULT NULL,
  `resolved_by_delivery_id` varchar(128) DEFAULT NULL,
  `resolved_at` timestamp(3) NULL DEFAULT NULL,
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `succeeded_at` timestamp(3) NULL DEFAULT NULL,
  `env` varchar(32) NOT NULL,
  PRIMARY KEY (`delivery_id`),
  UNIQUE KEY `uk_event_delivery_target` (`env`, `fanout_target_id`),
  KEY `idx_event_claim_due` (`env`, `status`, `next_attempt_at`, `lease_until`),
  KEY `idx_event_strict_lane` (`env`, `subscription_id`, `stream_key`, `status`, `sequence`),
  KEY `idx_event_delivery_subscription` (`env`, `subscription_id`, `status`, `created_at`),
  KEY `idx_event_delivery_replay` (`env`, `replay_of_delivery_id`, `status`),
  KEY `idx_event_delivery_retention` (`env`, `status`, `succeeded_at`, `dead_lettered_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_delivery_attempts` (
  `delivery_id` varchar(128) NOT NULL,
  `attempt_no` bigint(20) unsigned NOT NULL,
  `started_at` timestamp(3) NOT NULL,
  `completed_at` timestamp(3) NULL DEFAULT NULL,
  `latency_ms` bigint(20) unsigned DEFAULT NULL,
  `result` varchar(32) DEFAULT NULL,
  `http_status` int DEFAULT NULL,
  `error_category` varchar(128) DEFAULT NULL,
  `error_summary` varchar(2048) DEFAULT NULL,
  `response_bytes_observed` bigint(20) unsigned DEFAULT NULL,
  `worker_id` varchar(256) NOT NULL,
  PRIMARY KEY (`delivery_id`, `attempt_no`),
  KEY `idx_event_attempt_result` (`result`, `started_at`)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS `bcs_event_subscription_audits` (
  `audit_id` varchar(128) NOT NULL,
  `subscription_id` varchar(128) NOT NULL,
  `revision` bigint(20) unsigned DEFAULT NULL,
  `action` varchar(64) NOT NULL,
  `actor_type` varchar(32) NOT NULL,
  `actor_id` varchar(256) NOT NULL,
  `reason` varchar(128) DEFAULT NULL,
  `details_json` text DEFAULT NULL,
  `created_at` timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `env` varchar(32) NOT NULL,
  PRIMARY KEY (`audit_id`),
  KEY `idx_event_audit_subscription` (`env`, `subscription_id`, `created_at`),
  KEY `idx_event_audit_actor` (`env`, `actor_type`, `actor_id`, `created_at`)
) DEFAULT CHARSET = utf8mb4;
