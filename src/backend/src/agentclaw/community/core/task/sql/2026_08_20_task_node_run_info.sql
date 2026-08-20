-- task_node_run_info: node runtime info, 1:N by retry per (task_id, node_id).
-- Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_node_run_info` (
    `id`                                 bigint(20)      NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `node_id`            varchar(128)  NOT NULL                COMMENT '节点 ID(1:1 task_node)',
    `task_id`            varchar(128)  NOT NULL                COMMENT 'task_id',
    `run_mode`           varchar(64)   DEFAULT NULL            COMMENT '执行模态：single_bot|coop_group|bbs',
    `assignee`           varchar(1024) DEFAULT NULL            COMMENT '执行者 bot_id / group_id',
    `output`             text          DEFAULT NULL            COMMENT '执行产出',
    `acceptance_result`  text          DEFAULT NULL            COMMENT '验收结果 JSON: {verdict,acceptances_metric,gaps}',
    `retry`                    int           DEFAULT 0               COMMENT '第几次重试',
    `session_id`            varchar(256)  DEFAULT NULL            COMMENT 'session_id',
    `extend_props`       text          DEFAULT NULL            COMMENT '扩展属性,json格式',
    `start_time`         bigint(20)    unsigned DEFAULT NULL   COMMENT '开始执行时间',
    `update_time`        bigint(20)    unsigned DEFAULT NULL   COMMENT '执行最近更新时间',
    `end_time`           bigint(20)    unsigned DEFAULT NULL   COMMENT '结束执行时间',
    `gmt_create`         timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`       timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_node` (`task_id`, `node_id`, `retry`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_task` (`task_id`),
    KEY `idx_assignee` (`assignee`),
    KEY `idx_run_mode_status_time` (`run_mode`, `start_time`)
) DEFAULT CHARSET = utf8mb4 COMMENT='节点运行时执行信息';