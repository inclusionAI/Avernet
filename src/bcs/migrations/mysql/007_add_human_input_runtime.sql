ALTER TABLE bcs_state_machine_node_runs
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(128) DEFAULT NULL COMMENT '状态机节点最终执行结果',
    ADD COLUMN IF NOT EXISTS responded_by VARCHAR(256) DEFAULT NULL COMMENT 'HumanInput 节点实际响应人 Actor ID';
