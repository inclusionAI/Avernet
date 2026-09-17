ALTER TABLE bcs_state_machine_node_runs
    ADD COLUMN outcome VARCHAR(128) DEFAULT NULL,
    ADD COLUMN responded_by VARCHAR(256) DEFAULT NULL;
