-- Production DDL for the expert-chat multi-session ownership index.
-- Apply before deploying Backend code that exposes the plural /sessions APIs.
-- Do not reuse the historical ac_expert_chat_sessions table: its schema and
-- single-session unique key are incompatible with this ownership index.
CREATE TABLE `ac_expert_chat_owned_sessions` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(64) NOT NULL COMMENT '当前会话所属用户ID',
  `bot_id` varchar(64) NOT NULL COMMENT '互动Bot ID',
  `owner_id` varchar(64) NOT NULL COMMENT '互动Bot所有者ID',
  `session_key` varchar(255) NOT NULL COMMENT 'Engine会话唯一标识',
  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE'
    COMMENT '会话归属记录状态：ACTIVE或DELETED',
  `env` varchar(50) NOT NULL COMMENT '运行环境标识',
  `gmt_create` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  `gmt_modified` timestamp NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP COMMENT '记录最后修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_bot_owner_env_session`
    (`user_id`, `bot_id`, `owner_id`, `env`, `session_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='互动Bot多会话归属关系表';
