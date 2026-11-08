CREATE TABLE IF NOT EXISTS `ac_forum_interaction` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID及事件稳定排序键',
    `actor_type`      varchar(16)  NOT NULL                COMMENT '互动主体类型：HUMAN/BOT',
    `actor_id`        varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                      COMMENT '互动主体标识：HUMAN工号或BOT ID',
    `action_type`     varchar(16)  NOT NULL                COMMENT '互动动作：VIEW/LIKE/FAVORITE/FOLLOW/ACCEPT',
    `target_type`     varchar(16)  NOT NULL                COMMENT '互动目标类型：TOPIC/POST/HUMAN/BOT',
    `target_id`       varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                      COMMENT '目标稳定标识：topic_id/post_id/HUMAN工号/BOT ID',
    `env`             varchar(20)  NOT NULL                COMMENT '环境分区',
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件创建时间',
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP
                      COMMENT '事件修改时间；正常实现等同于创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_forum_interaction_target`
        (`env`, `target_type`, `target_id`, `action_type`, `gmt_create`, `id`),
    KEY `idx_forum_interaction_actor`
        (`env`, `actor_type`, `actor_id`, `action_type`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='BBS通用互动事件表';
