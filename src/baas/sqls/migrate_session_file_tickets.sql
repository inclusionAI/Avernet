-- Migration: Session 文件分享记录表
--
-- 设计取舍：**新建独立表 baas_session_file_tickets**，不修改 Bot 版
-- baas_file_transfer_tickets。原因：
--   1. Session 文件分享和 Bot 文件传输是两个独立场景，模型不同
--      （session_id 替代 paas_device_id，无 direction/device_path/download_url）；
--   2. 规避对 Bot 热表的在线 DDL 风险；
--   3. 状态机更简单（6 状态 vs 9 状态），CAS 转换图独立演化。
--
-- baas_file_transfer_tickets（不动） = Bot 文件传输记录，双向（上传/下载），9 状态。
-- baas_session_file_tickets（新）       = Session 文件分享记录，仅上传，6 状态。
--
-- 应用方式：与 sqls/ 下其他迁移一致，手工应用到 OceanBase（无 Alembic）。
-- SQLite 测试环境由 ORM Base.metadata.create_all() 自动建表，无需本文件。
-- 部署顺序：本迁移须在发布新版应用代码之前应用（新代码会读写本表）。
-- 向后兼容：新表，不影响已有 baas_file_transfer_tickets。

-- ---------------------------------------------------------------------------
-- Session 文件分享记录表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `baas_session_file_tickets` (
  `id`              bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `gmt_create`      timestamp           NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`    timestamp           NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `transfer_id`     varchar(128)        NOT NULL                                 COMMENT '传输唯一标识',
  `tenant`          varchar(128)        NOT NULL                                 COMMENT '租户',
  `session_id`      varchar(256)        NOT NULL                                 COMMENT '文件属于的会话标识',  -- 替换 paas_device_id
  `status`          varchar(32)         NOT NULL                                 COMMENT '文件状态: CREATED/UPLOADING/DONE/FAILED/CANCELLED/DELETED',
  `staging_subdir`  varchar(1024)       DEFAULT NULL                             COMMENT '虚拟子目录,用户可自行构造目录层级概念',
  `filename`        varchar(512)        NOT NULL                                 COMMENT '文件名',
  -- 无 device_path 列（Session 无本地设备路径概念）
  `fileservice_staging_path` varchar(1024) NOT NULL                              COMMENT 'FileService存储路径，内部实现概念，不对外暴露',
  `error_message`   text                DEFAULT NULL                             COMMENT '错误信息',
  `multipart_session_id` varchar(256)   DEFAULT NULL                             COMMENT '分片上传会话 ID',
  -- 无 download_url 列（同步 share-link，不存储）
  `env`             varchar(16)         NOT NULL                                 COMMENT '环境标识',
  `operator`        varchar(256)        NOT NULL DEFAULT 'unknown'               COMMENT '操作者',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tid`            (`transfer_id`)       BLOCK_SIZE 16384 GLOBAL,
  KEY       `idx_env_tnt_sid`    (`env`, `tenant`, `session_id`)     BLOCK_SIZE 16384 GLOBAL,
  KEY       `idx_env_tnt_dir`    (`env`, `tenant`, `staging_subdir`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT = 1 AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '会话级文件分享记录表';