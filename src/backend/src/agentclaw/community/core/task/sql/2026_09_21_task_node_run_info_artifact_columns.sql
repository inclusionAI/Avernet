-- task_node_run_info: add artifact double-write columns (阶段一,产物领域对象设计 §12).
-- output_artifact_ids: JSON list of ArtifactId published for this (task_id, node_id, retry);
-- primary_output_artifact_id: the artifact serving as the node's default final output / UI
-- primary display (最新发布者;文档 §8). Both NULL on legacy rows → the projection layer
-- reads them as empty/None (纯增量,旧读写路径零改动).
-- Follows the ALTER pattern of 2026_09_01_task_node_logical_delete.sql.
ALTER TABLE `task_node_run_info`
    ADD COLUMN `output_artifact_ids` text COMMENT '节点产物ID JSON list(阶段一 Artifact 双写;旧行 NULL)' ,
    ADD COLUMN `primary_output_artifact_id` varchar(128) DEFAULT NULL COMMENT '主产物ID(最终输出/UI 主展示)';