-- task_artifact: immutable artifact manifest (节点产物不可变清单).
-- 权威源: 语雀《BCN 产物(Artifact)领域对象设计》mad/enxdbg/gfap3r7tgc2aeptr §4/§9.
-- Append-only: no update path — content changes insert a new row whose supersedes
-- points at the old one. Only the deterministic artifact_id is unique (幂等承载),
-- no composite unique on (run_id, node_id, attempt) so one node may emit N artifacts.
-- content/lineage/created_by hold parsed JSON on the record layer; the content
-- JSON carries the mutually exclusive "kind" discriminator (text|structured|file|collection).
-- Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_artifact` (
    `id`                                bigint(20)      NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `artifact_id`       varchar(128)  NOT NULL                COMMENT '产物ID(确定性派生,重放幂等)',
    `task_id`           varchar(128)  NOT NULL                COMMENT 'task_id',
    `node_id`           varchar(128)  NOT NULL                COMMENT '产出节点 ID',
    `session_id`        varchar(256)  DEFAULT NULL            COMMENT '协作上下文 session_id',
    `run_id`            varchar(512)  DEFAULT NULL            COMMENT '框架图实例 run_id',
    `attempt`           int           DEFAULT 0               COMMENT '节点执行序(harness_retries 快照)',
    `artifact_kind`     varchar(64)   NOT NULL                COMMENT '业务用途(analysis_report|summary|source_code|chart|dataset|other)',
    `content`           mediumtext    NOT NULL                COMMENT 'ArtifactContent JSON(kind 判别,四分支互斥)',
    `lineage`           text          DEFAULT NULL            COMMENT '血缘 JSON: {"derived_from":[],"source_message_ids":[]}',
    `supersedes`        varchar(128)  DEFAULT NULL            COMMENT '被本产物替代的上一份产物ID(内容变化→新行)',
    `created_by`        text          NOT NULL                COMMENT '创建者 JSON: {"actor_type":"bot","actor_id":"bot:owner"}',
    `created_at`        bigint(20)    unsigned NOT NULL        COMMENT '创建时间(epoch 毫秒)',
    `gmt_create`        timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_artifact` (`artifact_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_artifact_task_node` (`task_id`, `node_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_artifact_session` (`session_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_artifact_supersedes` (`supersedes`) BLOCK_SIZE 16384 LOCAL
) DEFAULT CHARSET = utf8mb4 COMMENT='节点产物不可变清单';