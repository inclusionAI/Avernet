-- 006_edge_permission.sql — 08-12 A2A edge-permission tables (friend unification).
-- Spec: docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md §3.1.
-- Applied externally (ops/CI); the bcs binary runs only SQLite migrations.

CREATE TABLE IF NOT EXISTS `edge_grants` (
  `edge_id`                  VARCHAR(48)  NOT NULL,
  `env`                      VARCHAR(16)  NOT NULL,
  `from_id`                  VARCHAR(256) NOT NULL,
  `to_id`                    VARCHAR(256) NOT NULL,
  `grant_kind`               VARCHAR(16)  NOT NULL,           -- permission_profile | rules
  `grant_ref_id`             VARCHAR(128) NOT NULL,
  `rules`                    JSON         DEFAULT NULL,
  `status`                   VARCHAR(16)  NOT NULL DEFAULT 'approved',
  `originator_policy_type`   VARCHAR(16)  NOT NULL DEFAULT 'any',
  `originator_policy_data`   JSON         DEFAULT NULL,
  `created_at`               BIGINT       NOT NULL,
  `updated_at`               BIGINT       NOT NULL,
  PRIMARY KEY (`edge_id`),
  UNIQUE KEY `ux_edge_from_to_env_ref` (`from_id`, `to_id`, `env`, `grant_ref_id`),
  KEY `idx_edge_from_env_status` (`from_id`, `env`, `status`),
  KEY `idx_edge_to_env_status`   (`to_id`, `env`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `permission_profiles` (
  `permission_profile_id`    VARCHAR(48)  NOT NULL,
  `bot_id`                   VARCHAR(256) NOT NULL,
  `env`                      VARCHAR(16)  NOT NULL,
  `name`                     VARCHAR(64)  NOT NULL DEFAULT 'default',
  `description`              VARCHAR(512) DEFAULT NULL,
  `rules_template`           JSON         NOT NULL,
  `revision`                 BIGINT       NOT NULL DEFAULT 1,
  `digest`                   VARCHAR(128) NOT NULL,
  `is_default`               TINYINT(1)   NOT NULL DEFAULT 0,
  `status`                   VARCHAR(16)  NOT NULL DEFAULT 'active',
  `created_by`               VARCHAR(64)  NOT NULL,
  `updated_by`               VARCHAR(64)  DEFAULT NULL,
  `created_at`               BIGINT       NOT NULL,
  `updated_at`               BIGINT       NOT NULL,
  PRIMARY KEY (`permission_profile_id`),
  UNIQUE KEY `ux_profile_bot_env_default` (`bot_id`, `env`, `is_default`, `status`),
  KEY `idx_profile_bot_env` (`bot_id`, `env`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `permission_requests` (
  `request_id`        VARCHAR(48)  NOT NULL,
  `edge_id`           VARCHAR(48)  DEFAULT NULL,
  `env`               VARCHAR(16)  NOT NULL,
  `from_id`           VARCHAR(256) NOT NULL,
  `to_id`             VARCHAR(256) NOT NULL,
  `request_kind`      VARCHAR(16)  NOT NULL,                  -- connect | permission_profile | rules | revoke
  `requested_ref_id`  VARCHAR(128) DEFAULT NULL,
  `requested_rules`   JSON         DEFAULT NULL,
  `message`           TEXT         DEFAULT NULL,
  `status`            VARCHAR(16)  NOT NULL DEFAULT 'pending',
  `decision_reason`   TEXT         DEFAULT NULL,
  `created_by`        VARCHAR(64)  NOT NULL,
  `decided_by`        VARCHAR(64)  DEFAULT NULL,
  `created_at`        BIGINT       NOT NULL,
  `updated_at`        BIGINT       NOT NULL,
  `decided_at`        BIGINT       DEFAULT NULL,
  PRIMARY KEY (`request_id`),
  KEY `idx_req_to_env_status` (`to_id`, `env`, `status`),
  KEY `idx_req_from_env_status` (`from_id`, `env`, `status`),
  KEY `idx_req_edge` (`edge_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `capabilities` (
  `capability_id`     VARCHAR(48)  NOT NULL,
  `bot_id`            VARCHAR(256) NOT NULL,
  `env`               VARCHAR(16)  NOT NULL,
  `tool`              VARCHAR(64)  NOT NULL,
  `operation`         VARCHAR(64)  DEFAULT NULL,
  `specifier_schema`  JSON         DEFAULT NULL,
  `source`            VARCHAR(16)  NOT NULL,                   -- system | agent_card | manual
  `status`            VARCHAR(16)  NOT NULL DEFAULT 'active',
  `raw_metadata`      JSON         DEFAULT NULL,
  `created_at`        BIGINT       NOT NULL,
  `updated_at`        BIGINT       NOT NULL,
  PRIMARY KEY (`capability_id`),
  KEY `idx_cap_bot_env` (`bot_id`, `env`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `authz_decision_logs` (
  `decision_id`   VARCHAR(48)  NOT NULL,
  `env`           VARCHAR(16)  NOT NULL,
  `task_id`       VARCHAR(128) DEFAULT NULL,
  `run_id`        VARCHAR(128) DEFAULT NULL,
  `from_id`       VARCHAR(256) NOT NULL,
  `to_id`         VARCHAR(256) NOT NULL,
  `originator`    VARCHAR(256) DEFAULT NULL,
  `context_type`  VARCHAR(16)  NOT NULL,
  `decision`      VARCHAR(16)  NOT NULL,
  `reason_code`   VARCHAR(64)  NOT NULL,
  `grant_refs`    JSON         NOT NULL,
  `context_json`  JSON         DEFAULT NULL,
  `created_at`    BIGINT       NOT NULL,
  PRIMARY KEY (`decision_id`),
  KEY `idx_adl_env_from_to` (`env`, `from_id`, `to_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- bcs_bots: 人方向加好友开关 + 是否需审批（解耦 visibility，spec §3.2）
ALTER TABLE `bcs_bots`
  ADD COLUMN `human_addable`   TINYINT(1)  NOT NULL DEFAULT 0,
  ADD COLUMN `friend_approval` VARCHAR(8)  NOT NULL DEFAULT 'auto';