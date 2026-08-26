-- task_node_relation: decomposition-tree edges. Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_node_relation` (
    `id`                        bigint(20)         NOT NULL AUTO_INCREMENT                         COMMENT '主键ID',
    `task_id`       varchar(128)     NOT NULL                              COMMENT '归属任务 ID',
    `src_node_id`   varchar(128)     NOT NULL                              COMMENT '父节点',
    `dst_node_id`   varchar(128)     NOT NULL                              COMMENT '子节点',
    `relation_type` varchar(64)      NOT NULL DEFAULT 'DEPENDENCY'         COMMENT '关系类型',
    `extend_props`  text          DEFAULT NULL                                                COMMENT '扩展信息',
    `gmt_create`    timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP     COMMENT '创建时间',
    `gmt_modified`  timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_src_dst` (`task_id`, `src_node_id`, `dst_node_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_src` (`task_id`, `src_node_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务节点关系(Relation)';