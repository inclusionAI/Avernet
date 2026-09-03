-- =============================================================================
-- Task Graph Shared Persistence & Recovery — FULL CREATE script (final shape)
-- =============================================================================
-- This file USED TO be an additive ALTER migration. Because the platform does not
-- allow modifying indexes via ALTER, this is now a complete CREATE script for a
-- FRESH install (or after the task tables have been dropped). It creates every
-- task graph table in its final shape: base columns + graph metadata/version/
-- lease columns + callback event-idempotency columns + corrected natural-key
-- unique indexes + the new append-only action-log table.
--
-- Run order is dependency-free (no FKs); any order is safe. Uses
-- `CREATE TABLE IF NOT EXISTS` so it is idempotent. OceanBase-specific index
-- modifiers (BLOCK_SIZE ... LOCAL) match the operator-provisioned prod DDL and
-- must stay in sync with the SQLAlchemy models in
-- core/task/repository/models.py (whose Base.metadata.create_all is the
-- singlebox SQLite fresh-install shape).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- task_info: task-level source record + shared graph state (SSOT for recovery)
--   status           = runtime Status (PENDING|PLANNING|RUNNING|DONE|SUCCESS|FAILED|HUNG|CANCELLED)
--   graph_version    = optimistic concurrency guard for graph mutations
--   lease_*          = recovery lease (owner / expiry / heartbeat)
--   graph_output / graph_extend_props = JSON-serialized TEXT
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `task_info` (
    `id`                                    bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `task_id`              varchar(128)     NOT NULL                COMMENT '任务id',
    `source_type`          varchar(128)     NOT NULL                COMMENT '触发渠道类型 bot|coop_group',
    `owner_user_id`        varchar(256)     NOT NULL                COMMENT 'userId',
    `owner_bot_id`         varchar(256)     NOT NULL                COMMENT 'botId',
    `execution_config`     text             DEFAULT NULL            COMMENT '用户指定的执行配置',
    `task_spec`            text             NOT NULL                COMMENT '任务信息',
    `status`               varchar(64)      NOT NULL                COMMENT '节点状态',
    `graph_run_id`         varchar(512)     DEFAULT NULL            COMMENT '图运行实例ID',
    `graph_loop_round`     int              NOT NULL DEFAULT 0      COMMENT '图级总轮次',
    `graph_output`         text             DEFAULT NULL            COMMENT '图级产出(JSON)',
    `graph_extend_props`   text             DEFAULT NULL            COMMENT '图级扩展属性(JSON,含__graph_status)',
    `graph_version`        bigint(20)       NOT NULL DEFAULT 0      COMMENT '图版本(乐观并发)',
    `lease_owner`          varchar(256)     DEFAULT NULL            COMMENT '恢复租约持有实例',
    `lease_until`          bigint(20)       DEFAULT NULL            COMMENT '租约到期时间(毫秒)',
    `heartbeat_at`         bigint(20)       DEFAULT NULL            COMMENT '最近心跳时间(毫秒)',
    `gmt_create`           timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`         timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_id` (`task_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_status` (`status`, `gmt_modified`),
    KEY `idx_task_info_graph_version` (`task_id`, `graph_version`),
    KEY `idx_task_info_recovery` (`status`, `lease_until`, `gmt_modified`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务来源信息';


-- -----------------------------------------------------------------------------
-- task_node: node spec + status. Natural key (task_id, node_id) is unique so
-- equal node ids in different tasks cannot conflict.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `task_node` (
    `id`                   bigint(20)   NOT NULL AUTO_INCREMENT         COMMENT '主键ID',
    `task_id`              varchar(128) NOT NULL                        COMMENT '归属任务ID',
    `node_id`              varchar(128) NOT NULL                        COMMENT '节点唯一实例ID',
    `task_spec`            text         NOT NULL                        COMMENT '任务信息',
    `status`               varchar(64)  NOT NULL                        COMMENT '节点状态',
    `is_deleted`           tinyint(1)    NOT NULL DEFAULT 0                 COMMENT '逻辑删除标记',
    `gmt_create`           timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`         timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_node_identity` (`task_id`, `node_id`),
    KEY `idx_task_status` (`task_id`, `status`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务执行节点';


-- -----------------------------------------------------------------------------
-- task_node_run_info: node runtime info, 1:N by retry per (task_id, node_id).
-- High-volume action history lives in task_action_log, NOT here.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `task_node_run_info` (
    `id`                  bigint(20)      NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `node_id`             varchar(128)    NOT NULL                COMMENT '节点 ID(1:1 task_node)',
    `task_id`             varchar(128)    NOT NULL                COMMENT 'task_id',
    `run_mode`            varchar(64)     DEFAULT NULL            COMMENT '执行模态：single_bot|coop_group|bbs',
    `assignee`            varchar(1024)   DEFAULT NULL            COMMENT '执行者 bot_id / group_id',
    `output`              text            DEFAULT NULL            COMMENT '执行产出',
    `acceptance_result`   text            DEFAULT NULL            COMMENT '验收结果 JSON: {verdict,acceptances_metric,gaps}',
    `retry`               int             DEFAULT 0               COMMENT '第几次重试',
    `session_id`          varchar(256)    DEFAULT NULL            COMMENT 'session_id',
    `extend_props`        text            DEFAULT NULL            COMMENT '扩展属性,json格式(含bbs_owner/dispatching等)',
    `start_time`          bigint(20)      unsigned DEFAULT NULL   COMMENT '开始执行时间',
    `update_time`         bigint(20)      unsigned DEFAULT NULL   COMMENT '执行最近更新时间',
    `end_time`            bigint(20)      unsigned DEFAULT NULL   COMMENT '结束时间',
    `gmt_create`          timestamp       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`        timestamp       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_node` (`task_id`, `node_id`, `retry`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_task` (`task_id`),
    KEY `idx_assignee` (`assignee`),
    KEY `idx_run_mode_status_time` (`run_mode`, `start_time`)
) DEFAULT CHARSET = utf8mb4 COMMENT='节点运行时执行信息';


-- -----------------------------------------------------------------------------
-- task_node_relation: decomposition-tree edges. Unique key includes task_id so
-- equal node ids in different tasks cannot conflict on (src,dst).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `task_node_relation` (
    `id`               bigint(20)    NOT NULL AUTO_INCREMENT                         COMMENT '主键ID',
    `task_id`          varchar(128)  NOT NULL                                        COMMENT '归属任务 ID',
    `src_node_id`      varchar(128)  NOT NULL                                        COMMENT '父节点',
    `dst_node_id`      varchar(128)  NOT NULL                                        COMMENT '子节点',
    `relation_type`    varchar(64)   NOT NULL DEFAULT 'DEPENDENCY'                   COMMENT '关系类型',
    `extend_props`     text          DEFAULT NULL                                    COMMENT '扩展信息',
    `gmt_create`       timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP             COMMENT '创建时间',
    `gmt_modified`     timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_src_dst` (`task_id`, `src_node_id`, `dst_node_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_src` (`task_id`, `src_node_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务节点关系(Relation)';


-- -----------------------------------------------------------------------------
-- task_callback: received callback audit + event-idempotency.
--   event_id        = per-event id; unique so duplicate callbacks ack idempotently.
--   process_status  = PROCESSED once the graph mutation committed in the same tx.
--   node_id NOT NULL (D5.1) + varchar(128) (D5.3) so uk_workflow_instance dedups.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `task_callback` (
    `id`                  bigint(20)    NOT NULL AUTO_INCREMENT                  COMMENT '主键ID',
    `invoker`             varchar(128)  NOT NULL                                 COMMENT '回调服务调用者',
    `run_id`              varchar(512)  NOT NULL                                 COMMENT '运行实例ID',
    `node_id`             varchar(128)  NOT NULL                                 COMMENT '内部节点ID',
    `main_session_id`     varchar(256)  NOT NULL                                 COMMENT '主session_id',
    `status`              varchar(64)   DEFAULT NULL                             COMMENT '状态',
    `orig_callback_data`  text          NOT NULL                                 COMMENT '原始上报数据',
    `execution_graph`     text          DEFAULT NULL                             COMMENT '解析之后的执行状态图',
    `result`              text          DEFAULT NULL                             COMMENT '产出结果',
    `result_success`      tinyint(1)    DEFAULT NULL                             COMMENT '是否成功',
    `exec_error`          text          DEFAULT NULL                             COMMENT '错误信息',
    `extend_props`        text          DEFAULT NULL                             COMMENT '扩展属性',
    `event_id`            varchar(256)  DEFAULT NULL                             COMMENT '回调事件ID(幂等键)',
    `process_status`      varchar(64)   DEFAULT NULL                             COMMENT '处理状态(PROCESSED=已落图)',
    `processed_at`        timestamp     NULL DEFAULT NULL                        COMMENT '处理完成时间',
    `gmt_create`          timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP       COMMENT '创建时间',
    `gmt_modified`        timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_workflow_instance` (`run_id`, `node_id`),
    KEY `idx_session_id` (`main_session_id`),
    UNIQUE KEY `uk_task_callback_event` (`event_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='节点执行回调记录';


-- -----------------------------------------------------------------------------
-- task_action_log: append-only, high-volume node action history.
-- Independent from task_node_run_info so normal Dashboard queries never scan it.
--   event_id                = insert idempotency (replayable)
--   (task_id,node_id,seq)   = per-node ordering
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `task_action_log` (
    `id`            bigint(20)    NOT NULL AUTO_INCREMENT,
    `event_id`      varchar(256)  NOT NULL                COMMENT '动作事件ID(幂等键)',
    `task_id`       varchar(128)  NOT NULL                COMMENT '归属任务ID',
    `node_id`       varchar(128)  NOT NULL                COMMENT '节点ID',
    `seq`           int           NOT NULL                COMMENT '节点内自增序号',
    `action`        varchar(64)   NOT NULL                COMMENT '动作类型',
    `loop_round`    int           DEFAULT NULL            COMMENT '图级轮次快照',
    `attempt`       int           NOT NULL DEFAULT 0      COMMENT '执行/规划重试序号',
    `status_from`   varchar(64)   DEFAULT NULL            COMMENT '动作前态',
    `status_to`     varchar(64)   DEFAULT NULL            COMMENT '动作后态',
    `payload`       text          NOT NULL                COMMENT '动作产出全量(JSON)',
    `instance_id`   varchar(256)  DEFAULT NULL            COMMENT '产生该动作的实例',
    `gmt_create`    timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
    `gmt_modified`  timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_action_event` (`event_id`),
    UNIQUE KEY `uk_task_node_action_seq` (`task_id`, `node_id`, `seq`),
    KEY `idx_task_action_task_node` (`task_id`, `node_id`, `seq`),
    KEY `idx_task_action_created` (`gmt_create`)
) DEFAULT CHARSET = utf8mb4 COMMENT='Task node action history';
