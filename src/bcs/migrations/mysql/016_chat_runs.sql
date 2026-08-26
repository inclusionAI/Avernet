-- Direct Chat async run governance (#1546).
-- MySQL-authoritative lifecycle + content record; a Redis hot cache (managed
-- by SqlChatRunRepo) holds the streaming overlay so per-token deltas do not
-- hit this table. See docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md.

CREATE TABLE IF NOT EXISTS `bcs_chat_runs` (
  `run_id`              VARCHAR(64)  NOT NULL,
  `bot_uuid`            VARCHAR(128) NOT NULL,
  `from_bot_id`         VARCHAR(128) NOT NULL,
  `session_key`         VARCHAR(128) NOT NULL,
  `state`               VARCHAR(16)  NOT NULL,
  `accumulated_content` MEDIUMTEXT,
  `error_message`       TEXT,
  `created_at_ms`       BIGINT       NOT NULL,
  `updated_at_ms`       BIGINT       NOT NULL,
  `completed_at_ms`     BIGINT,
  `expires_at_ms`       BIGINT       NOT NULL,
  `version`             BIGINT       NOT NULL,
  `content_truncated`   TINYINT      NOT NULL DEFAULT 0,
  `client`              VARCHAR(64),
  `response_mode`       VARCHAR(32)  NOT NULL,
  `completion_policy`   VARCHAR(32)  NOT NULL,
  `delivery_ack_at_ms`  BIGINT,
  PRIMARY KEY (`run_id`),
  KEY `idx_chat_runs_expires` (`state`, `expires_at_ms`),
  KEY `idx_chat_runs_completed` (`state`, `completed_at_ms`),
  KEY `idx_chat_runs_from_bot` (`from_bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;