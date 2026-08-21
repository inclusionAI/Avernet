CREATE TABLE IF NOT EXISTS `baas_bot_run_interaction` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `session_key` varchar(512) NOT NULL COMMENT 'engine sessionKey',
  `interaction_id` varchar(160) NOT NULL COMMENT 'engine interactionId',
  `state` varchar(32) NOT NULL COMMENT 'requested/queued/dispatching/resolved/expired/failed',
  `payload` JSON NOT NULL COMMENT '完整 interaction 协议快照 JSON',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_interaction` (`session_key`, `interaction_id`),
  KEY `idx_session_state` (`session_key`, `state`)
) AUTO_INCREMENT = 1 DEFAULT CHARSET = utf8mb4 COMMENT = 'Bot run human interaction state';
