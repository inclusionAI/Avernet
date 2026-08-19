-- Migration v7: Add triggered_by, identity_key, current_phase to flow_runs;
--               Add node_title, progress_message to node_executions.
-- Target: SQLite (engine.db)

-- flow_runs: identity_key (triggered_by already exists in SQLite schema)
ALTER TABLE flow_runs ADD COLUMN identity_key VARCHAR(255);
ALTER TABLE flow_runs ADD COLUMN current_phase VARCHAR(255);

-- node_executions: node_title, progress_message
ALTER TABLE node_executions ADD COLUMN node_title VARCHAR(255);
ALTER TABLE node_executions ADD COLUMN progress_message TEXT;