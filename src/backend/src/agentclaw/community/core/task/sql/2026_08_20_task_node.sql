-- task_node: node spec + status. Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_node` (
    `id`                         bigint(20)         NOT NULL AUTO_INCREMENT         COMMENT '主键ID',
    `task_id`        varchar(128)      NOT NULL                    COMMENT '归属任务ID',
    `node_id`        varchar(128)      NOT NULL                    COMMENT '节点唯一实例ID',
    `task_spec`         text           NOT NULL                            COMMENT '任务信息',
    `status`         varchar(64)       NOT NULL                    COMMENT '节点状态',
    `gmt_create`     timestamp         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`   timestamp         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    KEY `idx_task_status` (`task_id`, `status`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务执行节点';