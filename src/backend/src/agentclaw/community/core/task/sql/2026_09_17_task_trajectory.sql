-- task_trajectory / task_trajectory_events: collected trajectory snapshots + analysis backfill.
-- Operator-provisioned in dev/pre/prod; SQLite tests use ORM metadata.
-- Independent旁路采集 entity: NO foreign key / NO association column to
-- task_action_log or task_callback (spec REQ-11 invariant).
-- Events are append-only (no unique constraint, duplicates accepted); the repo
-- (P1b) ALWAYS supplies gmt_create from the domain int-ms timestamp and does
-- NOT rely on the DB DEFAULT CURRENT_TIMESTAMP (kept only as a fallback).
CREATE TABLE IF NOT EXISTS `task_trajectory` (
    `id`         bigint(20)   NOT NULL AUTO_INCREMENT                            COMMENT '主键ID',
    `task_id`    varchar(128) NOT NULL                                           COMMENT '任务 ID(一任务一行)',
    `analysis`   text         DEFAULT NULL                                       COMMENT '内嵌 TrajectoryAnalysis JSON 字符串(未分析为 NULL,分析回填时写入)',
    `gmt_create` timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP                 COMMENT '组装产出时间',
    `gmt_modified` timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(分析回填时间)',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_trajectory_task` (`task_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务轨迹头行(组装时 UPSERT,分析回填 analysis)';

CREATE TABLE IF NOT EXISTS `task_trajectory_events` (
    `id`            bigint(20)   NOT NULL AUTO_INCREMENT                         COMMENT '主键ID',
    `task_id`       varchar(128) NOT NULL                                        COMMENT '归属任务 ID',
    `node_id`       varchar(128) NOT NULL                                        COMMENT '节点 ID',
    `action_type`   varchar(64)  NOT NULL                                        COMMENT '动作类型(submit|plan|dispatch|execute|verify|reset|transition)',
    `action_input`  text         DEFAULT NULL                                    COMMENT '动作输入内容(submit=task_spec_digest;plan=prompt_digest;dispatch=下发对象/节点规格;execute/verify=下发请求原文;reset/transition=NULL)',
    `action_result` text         DEFAULT NULL                                    COMMENT '动作结果枚举(success|hit_single|hit_multi|miss|failed|sla_timeout|pending_dispatch_stuck|exec_failed_retry|...)',
    `status_from`   varchar(64)  DEFAULT NULL                                    COMMENT '动作前节点状态',
    `status_to`     varchar(64)  DEFAULT NULL                                    COMMENT '动作后节点状态',
    `attempt`       int          NOT NULL DEFAULT 0                              COMMENT 'harness 重试序号快照',
    `boost_reason`  text         DEFAULT NULL                                    COMMENT '任务推进理由(本事件驱动任务推进的缘由文本;无理由为 NULL)',
    `error_type`    varchar(64)  DEFAULT NULL                                    COMMENT 'ReasonCatalog 错误分类(成功为 NULL)',
    `error_msg`     text         DEFAULT NULL                                    COMMENT '截断后的错误消息(成功为 NULL)',
    `ext_info`      text         DEFAULT NULL                                    COMMENT '扩展信息 JSON(DispatchRationale/RESET计量/SUBMIT来源等(后续可扩展素材);带 schema_v;领域对象不映射,analyzer 按需读)',
    `analysis`      text         DEFAULT NULL                                    COMMENT '内嵌 TrajectoryAnalysis JSON 字符串(未回填为 NULL,分析回填时写入)',
    `gmt_create`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP              COMMENT '事件发生时间(timeline 排序依据)',
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(分析回填时间)',
    PRIMARY KEY (`id`),
    KEY `idx_task_trajectory_events_task` (`task_id`, `gmt_create`),
    KEY `idx_task_trajectory_events_node` (`task_id`, `node_id`, `gmt_create`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务轨迹事件投影行(TrajectoryEvent 物化)';
