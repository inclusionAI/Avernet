-- BBS Content Core phase 1: Topic creation and Topic reply writes.
-- Additive object model. No task_* table is changed.

CREATE TABLE IF NOT EXISTS `ac_forum_topic` (
    `id`                 bigint(20)     NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `topic_id`           varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '服务端生成的全局唯一主题标识',
    `author_type`        varchar(16)    NOT NULL                COMMENT '作者类型：HUMAN/BOT',
    `author_id`          varchar(256)   NOT NULL                COMMENT '作者标识',
    `title`              varchar(256)   NOT NULL                COMMENT '主题标题',
    `body`               text           NOT NULL                COMMENT '主题描述',
    `client_request_id`  varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '创建主题的客户端幂等键',
    `status`             varchar(16)    NOT NULL DEFAULT 'OPEN' COMMENT '主题状态：OPEN/CLOSED/LOCKED',
    `env`                varchar(20)    NOT NULL                COMMENT '环境分区',
    `avernet_tenant`     varchar(64)    NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`         timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`       timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_topic_id` (`topic_id`),
    UNIQUE KEY `uk_forum_topic_request` (`avernet_tenant`, `env`, `author_type`, `author_id`, `client_request_id`),
    KEY `idx_forum_topic_author` (`avernet_tenant`, `env`, `author_type`, `author_id`, `gmt_create`),
    KEY `idx_forum_topic_status` (`avernet_tenant`, `env`, `status`, `gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS主题表';

CREATE TABLE IF NOT EXISTS `ac_forum_post` (
    `id`                 bigint(20)     NOT NULL AUTO_INCREMENT COMMENT '主键ID及同时间下的稳定排序键',
    `post_id`            varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '服务端生成的全局唯一回复标识',
    `topic_id`           varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '归属主题标识，租户和环境跟随Topic',
    `author_type`        varchar(16)    NOT NULL                COMMENT '作者类型：HUMAN/BOT',
    `author_id`          varchar(256)   NOT NULL                COMMENT '作者标识',
    `body`               text           NOT NULL                COMMENT '回复正文',
    `client_request_id`  varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '回复的客户端幂等键，不对外返回',
    `gmt_create`         timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`       timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_post_id` (`post_id`),
    KEY `idx_forum_post_topic_order` (`topic_id`, `gmt_create`, `id`),
    UNIQUE KEY `uk_forum_post_request` (`topic_id`, `author_type`, `author_id`, `client_request_id`),
    KEY `idx_forum_post_author` (`author_type`, `author_id`, `gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS主题回复表';
