-- REVIEW ONLY: NOT executed. OceanBase MySQL-mode deployment DDL.
-- This file creates only current selection; no ac_space changes.
-- Publication-query indexes are deferred separately in
-- 2026_10_22_bot_knowledge_publish_indexes.sql. Do not execute them with this file.
CREATE TABLE `ac_bot_knowledge_binding` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间',
  `avernet_tenant` varchar(64) COLLATE utf8mb4_bin NOT NULL DEFAULT 'teamclaw' COMMENT 'Avernet数据隔离租户',
  `env` varchar(20) COLLATE utf8mb4_bin NOT NULL COMMENT '环境标识',
  `bot_pk` bigint(20) NOT NULL COMMENT 'Bot记录主键，对应ac_bots.id',
  `space_id` bigint(20) NOT NULL COMMENT '绑定所属空间ID',
  `kb_id` varchar(128) COLLATE utf8mb4_bin NOT NULL COMMENT '知识库ID',
  `enabled` boolean NOT NULL DEFAULT FALSE COMMENT '是否启用该知识库',
  `revision` bigint(20) NOT NULL COMMENT '当前绑定版本号，用于并发修改校验',
  `updated_by` varchar(128) COLLATE utf8mb4_bin NOT NULL COMMENT '最后修改人标识',
  PRIMARY KEY(`id`),
  UNIQUE KEY `uk_bot_knowledge_binding`(`avernet_tenant`, `env`, `bot_pk`, `kb_id`) GLOBAL,
  KEY `idx_bk_enabled`(`avernet_tenant`, `env`, `bot_pk`, `space_id`, `enabled`, `kb_id`) GLOBAL,
  KEY `idx_bk_page`(`avernet_tenant`, `env`, `bot_pk`, `space_id`, `id`) GLOBAL
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_bin COMMENT = 'Bot知识库当前启用配置';
