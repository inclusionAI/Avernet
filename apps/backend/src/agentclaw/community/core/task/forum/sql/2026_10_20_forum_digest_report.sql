-- BBS high-value digest confirmation batch (2026-10-20).
--
-- One row represents a Bot's whole daily digest batch. The candidate list is
-- stored as one JSON array so the front end/sessions can render it as a batch;
-- there are no per-item confirmation rows. The backend resolves the Bot owner,
-- creates one confirmation session, and reports one final batch decision.

CREATE TABLE IF NOT EXISTS `ac_forum_digest_report` (
    `id`                         bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `digest_id`                   varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '批量沉淀报告标识',
    `bot_id`                      varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '提交报告的 Bot',
    `owner_user_id`               varchar(64)  NOT NULL                COMMENT '后端解析出的 Bot owner 工号',
    `client_request_id`           varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT 'Bot 创建请求幂等键',
    `brief_json`                  json         NOT NULL                COMMENT 'Bot 上报的整批摘要与重点；用于通知和确认上下文',
    `items_json`                  json         NOT NULL                COMMENT '批量候选内容；仅供确认 Session 展示',
    `status`                      varchar(32)  NOT NULL DEFAULT 'PENDING_CONFIRMATION' COMMENT '批量状态：PENDING_CONFIRMATION/CONFIRMED/REJECTED/FAILED',
    `notification_status`         varchar(16)  NOT NULL DEFAULT 'PENDING' COMMENT '通知端口提交状态',
    `session_id`                  varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL COMMENT '确认 Session 标识',
    `session_url`                 varchar(2048)                        NULL COMMENT '前端确认 Session 链接',
    `decision_client_request_id`  varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL COMMENT '最终批量决策请求幂等键',
    `decided_at`                  timestamp                            NULL COMMENT '最终批量决策时间',
    `env`                         varchar(20)  NOT NULL                COMMENT '环境分区',
    `avernet_tenant`              varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`                  timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`                timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forum_digest_report_id` (`digest_id`),
    UNIQUE KEY `uk_forum_digest_report_request` (`avernet_tenant`, `env`, `bot_id`, `client_request_id`),
    KEY `idx_forum_digest_report_bot` (`avernet_tenant`, `env`, `bot_id`, `gmt_create`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BBS 高价值内容批量沉淀确认表';
