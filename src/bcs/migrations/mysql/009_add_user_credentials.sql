-- Table: bcs_user_credentials
-- Password credentials for username/password auth (auth_source = "password" in
-- bcs_user_identities). Stores only the argon2 PHC hash; never the plaintext.
-- `username` is denormalized here (also in bcs_user_identities.external_user_id)
-- so login is a single indexed lookup; usernames are immutable so the two
-- copies never diverge.
CREATE TABLE IF NOT EXISTS `bcs_user_credentials` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `user_id` varchar(32) NOT NULL COMMENT '关联 bcs_user_identities.user_id',
  `username` varchar(64) NOT NULL COMMENT '登录用户名(不可变)',
  `password_hash` varchar(256) NOT NULL COMMENT 'argon2 PHC 串(含 salt+params)',
  `env` varchar(64) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_creds_user` (`user_id`, `env`),
  UNIQUE KEY `uk_user_creds_username` (`username`, `env`),
  KEY `idx_user_creds_env` (`env`)
) DEFAULT CHARSET = utf8mb4;
