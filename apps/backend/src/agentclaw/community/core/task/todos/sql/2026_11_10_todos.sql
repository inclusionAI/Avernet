-- Todo workbench A-side persistence. These additive tables own the product
-- Todo and acceptance decisions. B owns Context, events, event sequencing,
-- and execution orchestration.

CREATE TABLE IF NOT EXISTS `ac_todo` (
    `id`                bigint(20)    NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `todo_id`           varchar(64)   NOT NULL                COMMENT '待办稳定标识',
    `space_id`          bigint(20)    NOT NULL                COMMENT '待办所属产品空间',
    `title`             varchar(200)  NOT NULL                COMMENT '待办标题',
    `description`       text          NOT NULL                COMMENT '待办描述',
    `bot_id`            varchar(256)  NOT NULL                COMMENT '选中的 Bot 标识',
    `bot_name`          varchar(256)  NOT NULL                COMMENT '创建或修改时的 Bot 名称快照',
    `status`            varchar(32)   NOT NULL                COMMENT 'pending/in_progress/pending_acceptance/completed',
    `collab_context_id` varchar(256)  DEFAULT NULL            COMMENT 'B 侧协作上下文标识',
    `created_by`        varchar(256)  NOT NULL                COMMENT '创建人标识',
    `env`               varchar(20)   NOT NULL                COMMENT '环境分区',
    `avernet_tenant`    varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`        timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_tenant_env_id` (`avernet_tenant`, `env`, `todo_id`),
    KEY `idx_todo_space_status` (`avernet_tenant`, `env`, `space_id`, `status`, `gmt_create`, `id`),
    KEY `idx_todo_space_list` (`avernet_tenant`, `env`, `space_id`, `gmt_create`, `id`),
    KEY `idx_todo_status_scan` (`avernet_tenant`, `env`, `status`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待办工作台主体表';

CREATE TABLE IF NOT EXISTS `ac_todo_acceptance` (
    `id`                bigint(20)    NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `acceptance_id`     varchar(64)   NOT NULL                COMMENT '验收记录稳定标识',
    `todo_id`           varchar(64)   NOT NULL                COMMENT '所属待办标识',
    `verdict`           varchar(32)   NOT NULL                COMMENT 'approved/rejected',
    `message`           text          NOT NULL                COMMENT '验收意见；无意见时为空字符串',
    `author_id`         varchar(256)  NOT NULL                COMMENT '验收人标识',
    `env`               varchar(20)   NOT NULL                COMMENT '环境分区',
    `avernet_tenant`    varchar(64)   NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`        timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_acceptance_tenant_env_id` (`avernet_tenant`, `env`, `acceptance_id`),
    KEY `idx_todo_acceptance_history` (`avernet_tenant`, `env`, `todo_id`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待办验收历史表';

CREATE TABLE IF NOT EXISTS `ac_todo_execution` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    `execution_id` varchar(64) NOT NULL,
    `todo_id` varchar(64) NOT NULL,
    `collab_context_id` varchar(256) NOT NULL,
    `run_id` varchar(256) DEFAULT NULL,
    `token_hash` varchar(64) NOT NULL COMMENT 'SHA-256; plaintext token is never persisted',
    `status` varchar(32) NOT NULL COMMENT 'active/replaced/completed',
    `executor_id` varchar(256) NOT NULL,
    `env` varchar(20) NOT NULL,
    `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_execution_tenant_env_id` (`avernet_tenant`, `env`, `execution_id`),
    KEY `idx_todo_execution_active` (`avernet_tenant`, `env`, `todo_id`, `status`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Todo Bot execution credentials';

CREATE TABLE IF NOT EXISTS `ac_todo_artifact` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    `artifact_id` varchar(64) NOT NULL,
    `todo_id` varchar(64) NOT NULL,
    `collab_context_id` varchar(256) NOT NULL,
    `execution_id` varchar(64) NOT NULL,
    `artifact_name` varchar(256) NOT NULL,
    `artifact_kind` varchar(32) NOT NULL COMMENT 'text/file/url_link/code',
    `content` text NOT NULL COMMENT 'Stable metadata JSON; file bytes stay in object storage',
    `content_hash` varchar(64) NOT NULL,
    `creator` varchar(256) NOT NULL,
    `env` varchar(20) NOT NULL,
    `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_artifact_tenant_env_id` (`avernet_tenant`, `env`, `artifact_id`),
    UNIQUE KEY `uk_todo_artifact_content` (`avernet_tenant`, `env`, `todo_id`, `execution_id`, `content_hash`),
    KEY `idx_todo_artifact_list` (`avernet_tenant`, `env`, `todo_id`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Todo reported artifacts';
