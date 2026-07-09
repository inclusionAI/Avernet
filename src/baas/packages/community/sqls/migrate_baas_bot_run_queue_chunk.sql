CREATE TABLE IF NOT EXISTS `baas_bot_run_queue_chunk` (
  `id`          bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create`  timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
   `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `run_id`      varchar(128) NOT NULL COMMENT '关联 baas_bot_run.run_id',
  `seq`         int(11) NOT NULL COMMENT 'chunk 序号，严格递增',
  `chunk_type`  varchar(16) NOT NULL COMMENT 'delta / final / error / usage / agent',
  `content`     mediumtext DEFAULT NULL COMMENT 'chunk 内容 (JSON 或纯文本)',
  `metadata`    text DEFAULT NULL COMMENT 'chunk 元数据 JSON (engine_frame 等)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_run_seq` (`run_id`, `seq`),
  KEY `idx_run_seq` (`run_id`, `seq`)
) AUTO_INCREMENT = 1 DEFAULT CHARSET = utf8mb4 COMMENT = 'Bot 队列流式 chunk';
