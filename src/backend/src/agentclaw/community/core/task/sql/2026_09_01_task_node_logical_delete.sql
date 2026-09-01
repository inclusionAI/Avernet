-- Add logical deletion support to task_node without removing historical rows.
-- Run once on existing deployments; CREATE TABLE definitions include this column
-- for fresh databases.
ALTER TABLE `task_node`
    ADD COLUMN `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标记';
