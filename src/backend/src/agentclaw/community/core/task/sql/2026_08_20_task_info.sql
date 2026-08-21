-- task_info: task-level source record. Operator-provisioned in prod (OceanBase).
-- The ORM (core/task/repository/models.py) renders a plain unique index; the
-- BLOCK_SIZE/LOCAL modifier below is OceanBase-only and not expressible in SQLAlchemy.
CREATE TABLE IF NOT EXISTS `task_info` (
    `id`                                    bigint(20)         NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `task_id`             varchar(128)  NOT NULL                  COMMENT '任务id',
    `source_type`                 varchar(128)  NOT NULL                  COMMENT '触发渠道类型 bot|coop_group',
    `owner_user_id`           varchar(256)  NOT NULL                  COMMENT 'userId',
    `owner_bot_id`             varchar(256)  NOT NULL                  COMMENT 'botId',
    `execution_config`    text          DEFAULT NULL              COMMENT '用户指定的执行配置',
    `task_spec`                    text          NOT NULL                      COMMENT '任务信息',
    `status`              varchar(64)   NOT NULL                COMMENT '节点状态',
    `gmt_create`          timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`        timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_id` (`task_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_status` (`status`, `gmt_modified`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务来源信息';