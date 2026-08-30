-- Direct Chat async run governance (#1546).
-- MySQL-authoritative lifecycle + content record; a Redis hot cache (managed
-- by SqlChatRunRepo) holds the streaming overlay so per-token deltas do not
-- hit this table. See docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md.
--
-- `env` scopes every row to its BCS environment (shared-DB multi-env isolation,
-- matching the convention in 003_add_organizations / bcs-session-store): all
-- repository queries carry `AND env = ?`, and cleanup/metrics scans process
-- only this environment's rows.

CREATE TABLE IF NOT EXISTS `bcs_chat_runs` (
  `env`                 VARCHAR(64)  NOT NULL,
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
  PRIMARY KEY (`env`, `run_id`),
  KEY `idx_chat_runs_env_expires` (`env`, `state`, `expires_at_ms`),
  KEY `idx_chat_runs_env_completed` (`env`, `state`, `completed_at_ms`),
  KEY `idx_chat_runs_env_from_bot` (`env`, `from_bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;