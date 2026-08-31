-- Direct Chat async run governance (#1546).
-- MySQL-authoritative lifecycle + content record; a Redis hot cache (managed
-- by SqlChatRunRepo) holds the streaming overlay so per-token deltas do not
-- hit this table. See docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md.
--
-- `env` scopes every row to its BCS environment (shared-DB multi-env isolation,
-- matching the convention in 003_add_organizations / bcs-session-store): all
-- repository queries carry `AND env = ?`, and cleanup/metrics scans process
-- only this environment's rows.
--
-- Aligned with the platform DBA's online DDL shape (gray-release batch 1):
-- surrogate `id` PK + UNIQUE(env, run_id); the internal `gmt_create`/
-- `gmt_modified` timestamp convention (DB-managed) replaces app-written
-- created_at_ms/updated_at_ms — the store never writes those columns and
-- derives the record's created_at_ms/updated_at_ms from them on read. Column
-- widths match the online table. Platform-specific syntax (GLOBAL indexes)
-- is added by the platform migration; this is the logical source-of-truth.

CREATE TABLE IF NOT EXISTS `bcs_chat_runs` (
  `id`                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `gmt_create`          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified`        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env`                 VARCHAR(64)  NOT NULL,
  `run_id`              VARCHAR(128) NOT NULL,
  `bot_uuid`            VARCHAR(128) NOT NULL,
  `from_bot_id`         VARCHAR(128) NOT NULL,
  `session_key`         VARCHAR(128) NOT NULL,
  `state`               VARCHAR(64)  NOT NULL,
  `accumulated_content` MEDIUMTEXT,
  `error_message`       MEDIUMTEXT,
  `original_request`    MEDIUMTEXT,
  `completed_at_ms`     BIGINT,
  `expires_at_ms`       BIGINT       NOT NULL,
  `version`             BIGINT       NOT NULL,
  `content_truncated`   TINYINT      NOT NULL DEFAULT 0,
  `client`              VARCHAR(128),
  `response_mode`       VARCHAR(128) NOT NULL,
  `completion_policy`   VARCHAR(64)  NOT NULL,
  `delivery_ack_at_ms`  BIGINT,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_run_id` (`env`, `run_id`),
  KEY `idx_env_expires`  (`env`, `state`, `expires_at_ms`),
  KEY `idx_env_completed` (`env`, `state`, `completed_at_ms`),
  KEY `idx_env_from_bot` (`env`, `from_bot_id`),
  KEY `idx_env_bot`      (`env`, `bot_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
