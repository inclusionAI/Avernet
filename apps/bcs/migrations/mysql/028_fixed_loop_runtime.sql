ALTER TABLE bcs_state_machine_definition_snapshots
    ADD COLUMN execution_plan_json JSON NULL,
    ADD COLUMN execution_plan_content_hash CHAR(64) NULL,
    ADD COLUMN execution_plan_compiler_version VARCHAR(128) NULL;

ALTER TABLE bcs_state_machine_node_runs
    ADD COLUMN failure_action VARCHAR(16) DEFAULT NULL,
    ADD COLUMN runtime_phase VARCHAR(32) DEFAULT NULL,
    ADD COLUMN recovery_lease_owner VARCHAR(64) DEFAULT NULL,
    ADD COLUMN recovery_lease_token BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN recovery_lease_until_ms BIGINT DEFAULT NULL;

ALTER TABLE bcs_state_machine_runs
    ADD INDEX idx_sm_runs_progression (env, status, record_status, run_id);

ALTER TABLE bcs_group_sessions
    ADD INDEX idx_session_running_recovery (env, session_kind, status, session_id);

CREATE TABLE bcs_collaboration_delivery_checkpoints (
    env VARCHAR(32) NOT NULL,
    operation_key VARCHAR(512) NOT NULL,
    aggregate_kind VARCHAR(32) NOT NULL,
    aggregate_id VARCHAR(128) NOT NULL,
    operation_kind VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    progress_json JSON DEFAULT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at_ms BIGINT NOT NULL,
    delivered_at_ms BIGINT DEFAULT NULL,
    node_id VARCHAR(128) DEFAULT NULL,
    aggregate_attempt INT DEFAULT NULL,
    deadline_ms BIGINT DEFAULT NULL,
    lease_owner VARCHAR(64) DEFAULT NULL,
    lease_token BIGINT NOT NULL DEFAULT 0,
    lease_until_ms BIGINT DEFAULT NULL,
    last_error TEXT DEFAULT NULL,
    INDEX idx_collaboration_checkpoints_run (env, aggregate_id, operation_kind, status),
    INDEX idx_collaboration_terminal_im_scan (env, operation_kind, status, aggregate_id),
    PRIMARY KEY (env, operation_key)
) DEFAULT CHARSET = utf8mb4;
