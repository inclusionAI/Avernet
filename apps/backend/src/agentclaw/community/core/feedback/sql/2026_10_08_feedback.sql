-- General user feedback: experimental-feature feedback collection, shared by
-- every product module (bbs, task, ...). The reporter (work-no) is declared by
-- the caller on the openapi surface, not re-verified here. Additive table; no
-- existing table is changed.

CREATE TABLE IF NOT EXISTS `ac_feedback` (
    `id`              bigint(20)     NOT NULL AUTO_INCREMENT COMMENT '主键ID，兼作对外返回的稳定标识',
    `reporter_id`     varchar(256)   NOT NULL                COMMENT '反馈人标识（人工号），由调用方声明',
    `module`          varchar(64)    NOT NULL                COMMENT '反馈模块：bbs / task / onboarding ...（开放字符串）',
    `content`         text           NOT NULL                COMMENT '反馈内容',
    `env`             varchar(20)    NOT NULL                COMMENT '环境分区',
    `avernet_tenant`  varchar(64)    NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`     timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`   timestamp      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    KEY `idx_feedback_module`  (`avernet_tenant`, `env`, `module`, `gmt_create`, `id`),
    KEY `idx_feedback_reporter`(`avernet_tenant`, `env`, `reporter_id`, `gmt_create`),
    KEY `idx_feedback_list`     (`avernet_tenant`, `env`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通用反馈表';
