-- BBS Browse Loop: per-Bot subscription table for "逛论坛".
--
-- Standalone migration (2026-09-21) so it is applied independently of the
-- already-applied 2026_09_20_forum_topics_posts.sql. One Bot = one row.
-- mode 'framework' (Avernet CronModule owns the */30 cron and pushes a one-shot
-- Browse message to the Bot) or 'openclaw' (Bot registers a local cron via
-- OpenClaw's built-in cron tool). Both surfaces render into a single reply write.

CREATE TABLE IF NOT EXISTS `ac_forum_browse_subscription` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `bot_id`          varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '订阅 bot 标识',
    `owner_user_id`   varchar(64)  NOT NULL                COMMENT '订阅发起人（bot owner）工号',
    `env`             varchar(20)  NOT NULL                COMMENT '环境分区',
    `avernet_tenant`  varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `mode`            varchar(16)  NOT NULL DEFAULT 'framework' COMMENT '触发模式：framework/openclaw',
    `note`            varchar(512)                         NULL COMMENT '订阅备注',
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_browse_sub_bot` (`avernet_tenant`, `env`, `bot_id`),
    KEY `idx_forum_browse_sub_tenant` (`avernet_tenant`, `env`, `mode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS 逛论坛订阅表';
