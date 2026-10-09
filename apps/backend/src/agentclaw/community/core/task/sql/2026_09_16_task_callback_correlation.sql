-- task_callback_correlation: 回调↔节点关联持久化(REQ-P1)。
-- Persists the callback↔(task, node, retry) correlation across restarts so the
-- assembler can re-attach in-flight callbacks arriving after a restart to the
-- right node by event_id (idempotent). Mirrors task_callback idiom:
-- PK id bigint autoincrement, UNIQUE KEY on event_id, utf8mb4.
-- The repo (P1b) ALWAYS supplies gmt_create from the domain int-ms timestamp
-- and does NOT rely on the DB DEFAULT CURRENT_TIMESTAMP (kept only as fallback).
CREATE TABLE IF NOT EXISTS `task_callback_correlation` (
    `id`                  bigint(20)   NOT NULL AUTO_INCREMENT                    COMMENT '主键ID',
    `event_id`            varchar(256) NOT NULL                                   COMMENT '回调事件 ID(自然主键,幂等去重)',
    `main_session_id`     varchar(256) NOT NULL                                   COMMENT '主 session_id',
    `task_id`             varchar(128) NOT NULL                                   COMMENT '归属任务 ID',
    `node_id`             varchar(128) NOT NULL                                   COMMENT '关联节点 ID',
    `retry`               int          NOT NULL DEFAULT 0                         COMMENT '节点重试序号',
    `gmt_create`          timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP         COMMENT '创建时间(注册时写入)',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_callback_correlation_event` (`event_id`),
    KEY `idx_task_callback_correlation_node` (`task_id`, `node_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='回调↔节点关联持久化(跨重启关联)';
