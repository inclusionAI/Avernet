CREATE TABLE IF NOT EXISTS `ac_task_discovery_lock` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env` varchar(20) COLLATE utf8mb4_bin NOT NULL COMMENT '环境标识: prod/pre/dev',
  `bot_id` varchar(256) COLLATE utf8mb4_bin NOT NULL COMMENT 'Bot ID',
  `discovery_date` varchar(10) COLLATE utf8mb4_bin NOT NULL COMMENT '发现日期 YYYY-MM-DD',
  `holder` varchar(256) COLLATE utf8mb4_bin NOT NULL COMMENT '持锁者 (hostname)',
  `lock_token` varchar(256) COLLATE utf8mb4_bin NOT NULL COMMENT '持锁令牌（fencing token，释放时比对）',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY(`id`),
  UNIQUE KEY `uk_env_bot_id_discovery_date`(`env`, `bot_id`, `discovery_date`) LOCAL
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_bin COMMENT = '任务发现-per-bot 分布式锁';
