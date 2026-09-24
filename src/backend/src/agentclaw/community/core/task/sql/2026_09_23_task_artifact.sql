-- task_artifact:不可变任务产物 manifest 表(spec:specs/2026-09-23-task-artifact-manifest/)。
-- Operator-provisioned in dev/pre/prod; SQLite tests build from ORM metadata
-- (Base.metadata.create_all) instead of this file, same as task_trajectory.
--
-- 特性:产物 = 业务身份(artifact_kind)+ kind-tagged 互斥 content(text|file)+
-- 作用域(task/node/attempt)+ 血缘(supersedes/derived_from)。文件字节不入
-- 本表 —— File 分支仅引用 ac_session_resource 的稳定 resource_id("sr_ 前缀);
-- 临时下载 URL / Token / 对象存储 key 禁止进表(访问能力按需动态签发)。
-- 不可变:内容修订 = 新行 + supersedes 指向旧行;旧行保留供审计回放。
-- 幂等:uk_task_artifact_dedupe 按内容寻址摘要收拢重复发布(fold 重放的库层兜底)。
CREATE TABLE IF NOT EXISTS `task_artifact` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `task_id` varchar(128) COLLATE utf8mb4_bin NOT NULL COMMENT '整体任务 id(任务唯一标识)',
  `node_id` varchar(128) COLLATE utf8mb4_bin NOT NULL COMMENT '产物归属节点 id(图级聚合产物挂根节点 id)',
  `artifact_id` varchar(128) COLLATE utf8mb4_bin NOT NULL COMMENT '产物稳定标识(art_ 前缀 + uuid hex)',
  `artifact_kind` varchar(64) NOT NULL COMMENT '业务用途轴:node_result|graph_rollup|internal_control(闭合枚举,增项须 spec 同步)',
  `content_kind` varchar(32) NOT NULL COMMENT '内容技术分支锚:text|file(Structured/Collection 后置 M2)',
  `content` mediumtext NOT NULL COMMENT 'kind-tagged 内容 dict 的 JSON 全文(text 存全文不截断;file 存 {resource_id,file_name,media_type,size_bytes,sha256})',
  `attempt` int NOT NULL DEFAULT '0' COMMENT '节点执行次数(harness_retries 口径);重试产生新 attempt 系列不覆盖旧行',
  `content_hash` char(71) NOT NULL COMMENT '内容寻址摘要 sha256:<hex64>(幂等 dedupe 锚;对齐 ac_manifest_content 惯例)',
  `supersedes` varchar(128) COLLATE utf8mb4_bin DEFAULT NULL COMMENT '同 (task,node,attempt) 内容演进时指向被替代的上一产物 id',
  `derived_from` text COMMENT '上游产物 id 的 JSON 数组(派生血缘,如 PDF→文本提取;M2)',
  `created_by` varchar(128) DEFAULT NULL COMMENT '产出者(bot_id / system / 人工 actor)',
  `created_at` bigint(20) NOT NULL COMMENT '业务毫秒时间戳(排序键,对齐轨迹 int-ms 约定)',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '落库时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间(不可变行:默认等于 gmt_create)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_artifact_id` (`artifact_id`),
  UNIQUE KEY `uk_task_artifact_dedupe` (`task_id`, `node_id`, `attempt`, `content_hash`),
  KEY `idx_task_artifact_node` (`task_id`, `node_id`, `attempt`, `created_at`),
  KEY `idx_task_artifact_task` (`task_id`, `created_at`)
) DEFAULT CHARSET = utf8mb4 COMMENT = '任务产物不可变 manifest(一行=一次发布)';