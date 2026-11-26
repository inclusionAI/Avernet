-- Add independent append-only G3 acquisition storage (OceanBase MySQL mode).
-- Apply once before using TelemetryService; SQLite tests create ORM metadata.
-- Does not update/backfill L4 Collet, Task, Event or legacy progress snapshots.
CREATE TABLE IF NOT EXISTS `ac_context_collab_telemetry` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `record_kind` varchar(32) NOT NULL COMMENT 'MetricDefinition, Observation, MetricSample or Outcome',
  `record_id` varchar(128) NOT NULL COMMENT 'Opaque ID; definition identity hashes metric_id and version',
  `collet_id` varchar(128) DEFAULT NULL COMMENT 'NULL only for unscoped metric definitions',
  `subject_type` varchar(32) DEFAULT NULL,
  `subject_id` varchar(128) DEFAULT NULL,
  `record_data` mediumtext NOT NULL COMMENT 'Protocol value JSON; definition includes its versioned reference',
  `telemetry_context` mediumtext DEFAULT NULL COMMENT 'TelemetryContext JSON; NULL for metric definitions',
  `recorded_by` varchar(2048) NOT NULL COMMENT 'Authenticated local recorder IdentityRef JSON',
  `recorded_at` datetime NOT NULL COMMENT 'Server acquisition time in UTC',
  `env` varchar(20) NOT NULL,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_telemetry` (`avernet_tenant`, `env`, `record_kind`, `record_id`) GLOBAL,
  KEY `idx_context_collab_telemetry_subject` (`avernet_tenant`, `env`, `collet_id`, `subject_type`, `subject_id`, `id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'G3 self-reported observations, metric definitions/samples and outcomes; not Work authority';
