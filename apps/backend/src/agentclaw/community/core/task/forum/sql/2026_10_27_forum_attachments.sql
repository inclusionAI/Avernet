-- BBS real file attachments for SHARE Topics (2026-10-27).
--
-- Upload creates an object-storage object plus one unbound metadata row. SHARE
-- Topic creation validates ownership and binds the selected rows to topic_id in
-- the same database transaction as the Topic insert. Topic JSON stores only
-- attachment ids; object keys and display metadata remain governed here.

CREATE TABLE IF NOT EXISTS `ac_forum_attachment` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `attachment_id`   varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '服务端生成的全局唯一附件标识',
    `author_type`     varchar(16)  NOT NULL COMMENT '上传者类型：HUMAN/BOT',
    `author_id`       varchar(256) NOT NULL COMMENT '上传者标识',
    `topic_id`        varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL COMMENT '绑定的 SHARE Topic；上传后创建 Topic 前为 NULL',
    `object_key`      varchar(512) NOT NULL COMMENT '对象存储内部键，不通过 Topic JSON 暴露',
    `filename`        varchar(255) NOT NULL COMMENT '原始文件名，最多 255 UTF-8 bytes',
    `content_type`    varchar(255) NOT NULL COMMENT '下载响应 MIME；未知时 application/octet-stream',
    `size_bytes`      bigint(20)   NOT NULL COMMENT '文件字节数；单文件上限 20 MiB',
    `env`             varchar(20)  NOT NULL COMMENT '环境分区',
    `avernet_tenant`  varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_attachment_id` (`attachment_id`),
    KEY `idx_forum_attachment_owner` (`avernet_tenant`, `env`, `author_type`, `author_id`, `topic_id`),
    KEY `idx_forum_attachment_topic` (`topic_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS SHARE 主题真实附件元数据表';
