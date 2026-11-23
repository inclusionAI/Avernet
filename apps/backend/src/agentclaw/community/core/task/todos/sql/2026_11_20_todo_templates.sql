-- Versioned Todo product-flow templates. Deployment applies this additive DDL
-- manually; no historical Todo rows are migrated by this change.

CREATE TABLE IF NOT EXISTS `ac_todo_template` (
    `id`                   bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `template_id`          varchar(64)  NOT NULL                COMMENT '模板稳定标识',
    `space_id`             bigint(20)   NOT NULL                COMMENT '模板所属产品空间',
    `name`                 varchar(200) NOT NULL                COMMENT '当前模板名称',
    `description`          text         NOT NULL                COMMENT '当前模板描述',
    `status`               varchar(32)  NOT NULL                COMMENT 'active/archived',
    `published_version_id` varchar(64)  DEFAULT NULL            COMMENT '当前已发布版本标识',
    `created_by`           varchar(256) NOT NULL                COMMENT '创建人标识',
    `updated_by`           varchar(256) NOT NULL                COMMENT '最后修改人标识',
    `env`                  varchar(20)  NOT NULL                COMMENT '环境分区',
    `avernet_tenant`       varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`           timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`         timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_template_tenant_env_id` (`avernet_tenant`, `env`, `template_id`),
    KEY `idx_todo_template_space_status` (`avernet_tenant`, `env`, `space_id`, `status`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待办产品流程模板';

CREATE TABLE IF NOT EXISTS `ac_todo_template_version` (
    `id`                  bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `template_version_id` varchar(64)  NOT NULL                COMMENT '模板版本稳定标识',
    `template_id`         varchar(64)  NOT NULL                COMMENT '所属模板稳定标识',
    `draft_template_id`   varchar(64)  DEFAULT NULL            COMMENT '草稿唯一占位；发布后置空',
    `version_no`          int(11)      NOT NULL                COMMENT '模板内单调递增版本号',
    `status`              varchar(32)  NOT NULL                COMMENT 'draft/published',
    `schema_version`      varchar(32)  NOT NULL                COMMENT '模板定义协议版本',
    `definition_json`     text         NOT NULL                COMMENT '规范化模板定义 JSON',
    `definition_hash`     varchar(64)  NOT NULL                COMMENT '规范化定义 SHA-256',
    `created_by`          varchar(256) NOT NULL                COMMENT '版本创建人标识',
    `published_by`        varchar(256) DEFAULT NULL            COMMENT '版本发布人标识',
    `published_at`        timestamp    NULL DEFAULT NULL       COMMENT '版本发布时间',
    `env`                 varchar(20)  NOT NULL                COMMENT '环境分区',
    `avernet_tenant`      varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '租户分区',
    `gmt_create`          timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`        timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_todo_template_version_tenant_env_id` (`avernet_tenant`, `env`, `template_version_id`),
    UNIQUE KEY `uk_todo_template_version_no` (`avernet_tenant`, `env`, `template_id`, `version_no`),
    UNIQUE KEY `uk_todo_template_single_draft` (`avernet_tenant`, `env`, `draft_template_id`),
    KEY `idx_todo_template_version_history` (`avernet_tenant`, `env`, `template_id`, `version_no`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待办产品流程模板版本';

-- The Todo lifecycle projection is intentionally additive. Existing historical
-- rows may be deleted by the deployment owner; no data migration is required.
-- For existing installations add these nullable columns to ac_todo:
-- ALTER TABLE ac_todo ADD COLUMN pending_action_kind varchar(32) NULL;
-- ALTER TABLE ac_todo ADD COLUMN state_reason text NULL;
-- ALTER TABLE ac_todo ADD COLUMN requested_action text NULL;
