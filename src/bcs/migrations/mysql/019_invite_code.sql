-- Invite-code access-gate support.
CREATE TABLE IF NOT EXISTS `bcs_invite_codes` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `code_hash` varchar(64) NOT NULL,
  `code_hint` varchar(16) NOT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'active',
  `bound_user_id` varchar(255) DEFAULT NULL,
  `bound_at` bigint DEFAULT NULL,
  `created_by` varchar(255) DEFAULT NULL,
  `env` varchar(32) NOT NULL,
  `gmt_create` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_invite_codes_code_hash` (`code_hash`),
  UNIQUE KEY `uk_invite_codes_bound_user_id` (`bound_user_id`),
  KEY `idx_invite_codes_env_status` (`env`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
