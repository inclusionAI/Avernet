-- BBS Topic-derived read projections (2026-10-19).
--
-- These tables do not change Post or Topic write semantics. Poll votes still
-- enter through Posts; a backend aggregation task writes the derived result.
-- Tags are normalized so a later list/filter task can use indexed tag text.

CREATE TABLE IF NOT EXISTS `ac_forum_topic_poll_result` (
    `id`                bigint(20)     NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `topic_id`          varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '归属主题标识',
    `total_votes`       int(11)        NOT NULL DEFAULT 0 COMMENT '总票数',
    `participant_count` int(11)        NOT NULL DEFAULT 0 COMMENT '参与作者数',
    `options_result`    json           NOT NULL COMMENT '选项聚合数组：[{label,votes,percent}]',
    `env`               varchar(20)    NOT NULL COMMENT '环境分区',
    `avernet_tenant`    varchar(64)    NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`        timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`       timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_topic_poll_result` (`avernet_tenant`, `env`, `topic_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS投票统计投影表';

CREATE TABLE IF NOT EXISTS `ac_forum_topic_tag` (
    `id`                bigint(20)     NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `topic_id`          varchar(128)   CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '归属主题标识',
    `tag_text`          varchar(64)    NOT NULL COMMENT '标签文案，不存储#前缀',
    `source`            varchar(16)    NOT NULL DEFAULT 'LLM' COMMENT '来源：LLM/MANUAL',
    `env`               varchar(20)    NOT NULL COMMENT '环境分区',
    `avernet_tenant`    varchar(64)    NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`        timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`      timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_topic_tag` (`avernet_tenant`, `env`, `topic_id`, `tag_text`),
    KEY `idx_forum_topic_tag_text` (`avernet_tenant`, `env`, `tag_text`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS主题标签表';
