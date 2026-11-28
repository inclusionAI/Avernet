
CREATE TABLE IF NOT EXISTS `ac_todo_source_import_rule` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `rule_id` varchar(64) NOT NULL COMMENT '自动导入规则稳定标识',
    `space_id` bigint(20) NOT NULL COMMENT '待办归属 TeamClaw 空间',
    `source_type` varchar(32) NOT NULL DEFAULT 'dima' COMMENT '来源类型',
    `source_container_ids_json` text NOT NULL COMMENT '待扫描 Dima 空间或项目 ID JSON',
    `filter_text` text NOT NULL COMMENT '外部 Agent 使用的自然语言过滤条件',
    `bot_id` varchar(256) NOT NULL COMMENT '导入待办的执行 Bot',
    `status` varchar(32) NOT NULL COMMENT 'enabled/disabled',
    `cursor_updated_at` timestamp(6) NULL DEFAULT NULL COMMENT '最近成功推进的来源更新时间游标',
    `created_by` varchar(256) NOT NULL COMMENT '规则创建人及 Agent 调用身份',
    `env` varchar(20) NOT NULL COMMENT '环境分区',
    `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_source_import_rule_id` (`avernet_tenant`, `env`, `rule_id`),
    KEY `idx_todo_source_import_rule_owner` (`avernet_tenant`, `env`, `created_by`, `status`, `gmt_modified`, `id`),
    KEY `idx_todo_source_import_rule_space` (`avernet_tenant`, `env`, `space_id`, `status`, `gmt_modified`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待办来源自动导入规则';

CREATE TABLE IF NOT EXISTS `ac_todo_source_import_progress` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `progress_id` varchar(64) NOT NULL COMMENT '处理进度稳定标识',
    `rule_id` varchar(64) NOT NULL COMMENT '所属自动导入规则',
    `source_container_id` varchar(128) NOT NULL COMMENT 'Dima 空间或项目 ID',
    `source_item_id` varchar(128) NOT NULL COMMENT 'Dima 工作项 ID',
    `source_updated_at` timestamp(6) NOT NULL COMMENT '本次处理的来源版本时间',
    `status` varchar(32) NOT NULL COMMENT 'imported/skipped/failed/duplicate',
    `todo_id` varchar(64) DEFAULT NULL COMMENT '成功或重复时关联的待办 ID',
    `detail` text NOT NULL COMMENT '失败或跳过原因',
    `env` varchar(20) NOT NULL COMMENT '环境分区',
    `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_source_import_progress_id` (`avernet_tenant`, `env`, `progress_id`),
    UNIQUE KEY `uk_todo_source_import_progress_version` (`avernet_tenant`, `env`, `rule_id`, `source_container_id`, `source_item_id`, `source_updated_at`),
    KEY `idx_todo_source_import_progress_rule` (`avernet_tenant`, `env`, `rule_id`, `source_updated_at`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待办来源自动导入处理进度';
