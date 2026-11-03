-- Caller group Human speaking leases (spec 2026-09-16 caller identity).
-- One row per environment + lease scope. The owning service passes the GROUP id
-- here: the column name is historical/protocol-compatible (`session_id`).
-- Renumbered 036 → 037 on the dev_caller_1023 rebase: upstream dev reserved
-- MySQL 036 for bot_authority.
CREATE TABLE IF NOT EXISTS `bcs_session_speaking_leases` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(64) NOT NULL,
  `session_id` varchar(256) NOT NULL,
  `holder_actor_id` varchar(256) NOT NULL DEFAULT '',
  `lease_id` varchar(64) NOT NULL DEFAULT '',
  `epoch` bigint(20) NOT NULL DEFAULT 0,
  `expires_at_ms` bigint(20) NOT NULL DEFAULT 0,
  `released` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_speaking_leases_env_session` (`env`, `session_id`),
  KEY `idx_speaking_leases_session` (`env`, `session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
