-- Migration v7: Add identity_key, current_phase to flow_runs;
--               Add progress_message to node_executions.
-- Target: MySQL / ZDAS (clawflow平台库)
-- Note: triggered_by and node_title already exist in ZDAS schema.

-- flow_runs: identity_key, current_phase (triggered_by already exists)
ALTER TABLE flow_runs ADD COLUMN identity_key VARCHAR(255) COMMENT '工作流身份键(用于分组)';
ALTER TABLE flow_runs ADD COLUMN current_phase VARCHAR(255) COMMENT '当前执行阶段';

-- node_executions: progress_message (node_title already exists)
ALTER TABLE node_executions ADD COLUMN progress_message TEXT COMMENT '进度消息';