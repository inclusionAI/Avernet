ALTER TABLE bcs_state_machine_definition_snapshots ADD COLUMN execution_plan_json TEXT DEFAULT NULL;

ALTER TABLE bcs_state_machine_definition_snapshots ADD COLUMN execution_plan_content_hash TEXT DEFAULT NULL;

ALTER TABLE bcs_state_machine_definition_snapshots ADD COLUMN execution_plan_compiler_version TEXT DEFAULT NULL;

ALTER TABLE bcs_state_machine_node_runs ADD COLUMN failure_action TEXT DEFAULT NULL;

ALTER TABLE bcs_state_machine_node_runs ADD COLUMN runtime_phase TEXT DEFAULT NULL;

ALTER TABLE bcs_state_machine_node_runs ADD COLUMN recovery_lease_owner TEXT DEFAULT NULL;

ALTER TABLE bcs_state_machine_node_runs ADD COLUMN recovery_lease_token INTEGER NOT NULL DEFAULT 0;

ALTER TABLE bcs_state_machine_node_runs ADD COLUMN recovery_lease_until_ms INTEGER DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_sm_runs_progression
    ON bcs_state_machine_runs(env, status, record_status, run_id);

CREATE INDEX IF NOT EXISTS idx_session_running_recovery
    ON bcs_group_sessions(env, session_kind, status, session_id);

CREATE TABLE IF NOT EXISTS bcs_collaboration_delivery_checkpoints (
    env TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    aggregate_kind TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    progress_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at_ms INTEGER NOT NULL,
    delivered_at_ms INTEGER DEFAULT NULL,
    node_id TEXT,
    aggregate_attempt INTEGER,
    deadline_ms INTEGER,
    lease_owner TEXT,
    lease_token INTEGER NOT NULL DEFAULT 0,
    lease_until_ms INTEGER,
    last_error TEXT,
    PRIMARY KEY (env, operation_key)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_checkpoints_run
    ON bcs_collaboration_delivery_checkpoints(env, aggregate_id, operation_kind, status);

CREATE INDEX IF NOT EXISTS idx_collaboration_terminal_im_scan
    ON bcs_collaboration_delivery_checkpoints(env, operation_kind, status, aggregate_id);
