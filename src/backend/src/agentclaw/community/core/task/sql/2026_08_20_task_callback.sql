-- task_callback: received callback audit. Operator-provisioned in prod (OceanBase).
-- D5.1: node_id is NOT NULL (was DEFAULT NULL) so uk_workflow_instance dedups.
-- D5.3: node_id is varchar(128) (was varchar(512)), consistent with task_node.node_id.
CREATE TABLE IF NOT EXISTS `task_callback` (
    `id`                                     bigint(20)         NOT NULL AUTO_INCREMENT                  COMMENT '主键ID',
    `invoker`                    varchar(128)  NOT NULL                         COMMENT '回调服务调用者',
    `run_id`                     varchar(512)     NOT NULL                         COMMENT '运行实例ID',
    `node_id`                 varchar(128)     NOT NULL                     COMMENT '内部',
    `main_session_id`       varchar(256)     NOT NULL                         COMMENT '主session_id',
    `status`                           varchar(64)     DEFAULT NULL                     COMMENT '状态',
    `orig_callback_data`  text          NOT NULL                         COMMENT '原始上报数据',
    `execution_graph`       text             DEFAULT NULL                     COMMENT '解析之后的执行状态图',
    `result`                     text             DEFAULT NULL                     COMMENT '产出结果',
    `result_success`             tinyint(1)       DEFAULT NULL                     COMMENT '是否成功',
    `exec_error`                 text                     DEFAULT NULL                     COMMENT '错误信息',
    `extend_props`                 text             DEFAULT NULL                               COMMENT '扩展属性',
    `gmt_create`                 timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`               timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_workflow_instance` (`run_id`, `node_id`),
    KEY `idx_session_id` (`main_session_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='节点执行回调记录';