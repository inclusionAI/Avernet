-- Production DDL for the expert-chat multi-session ownership index.
-- Apply before deploying Backend code that exposes the plural /sessions APIs.
CREATE TABLE IF NOT EXISTS `ac_expert_chat_sessions` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `user_id` varchar(64) NOT NULL,
  `bot_id` varchar(64) NOT NULL,
  `owner_id` varchar(64) NOT NULL,
  `session_key` varchar(255) NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE',
  `env` varchar(50) NOT NULL,
  `gmt_create` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_bot_owner_env_session`
    (`user_id`, `bot_id`, `owner_id`, `env`, `session_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
