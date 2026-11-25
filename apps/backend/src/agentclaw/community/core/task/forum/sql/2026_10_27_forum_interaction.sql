CREATE TABLE IF NOT EXISTS `ac_forum_interaction` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID及状态稳定排序键',
    `actor_type`      varchar(16)  NOT NULL                COMMENT '互动主体类型：HUMAN/BOT',
    `actor_id`        varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                      COMMENT '互动主体标识：HUMAN工号或BOT ID',
    `action_type`     varchar(16)  NOT NULL                COMMENT '互动动作：VIEW/LIKE/FAVORITE/FOLLOW/ACCEPT',
    `target_type`     varchar(16)  NOT NULL                COMMENT '互动目标类型：TOPIC/POST/HUMAN/BOT',
    `target_id`       varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                      COMMENT '目标稳定标识：topic_id/post_id/HUMAN工号/BOT ID',
    `env`             varchar(20)  NOT NULL                COMMENT '环境分区',
    `active`          int          NOT NULL DEFAULT 1      COMMENT '互动累计有效量：>0参与计数，0无效',
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '状态创建时间（北京时间）',
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP
                      COMMENT '状态修改时间（北京时间）；active 更新时刷新',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_interaction_action`
        (`env`, `actor_type`, `actor_id`, `action_type`, `target_type`, `target_id`),
    KEY `idx_forum_interaction_target`
        (`env`, `target_type`, `target_id`, `action_type`, `gmt_create`, `id`),
    KEY `idx_forum_interaction_actor`
        (`env`, `actor_type`, `actor_id`, `action_type`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='BBS通用互动状态表';
