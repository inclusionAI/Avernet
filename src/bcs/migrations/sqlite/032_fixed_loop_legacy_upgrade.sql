ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN progress_json TEXT;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN node_id TEXT;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN aggregate_attempt INTEGER;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN deadline_ms INTEGER;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN lease_owner TEXT;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN lease_token INTEGER NOT NULL DEFAULT 0;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN lease_until_ms INTEGER;

ALTER TABLE bcs_collaboration_delivery_checkpoints ADD COLUMN last_error TEXT;
