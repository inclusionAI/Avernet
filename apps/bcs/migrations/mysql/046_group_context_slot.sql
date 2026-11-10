ALTER TABLE bcs_group_contexts ADD COLUMN is_slot BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS bcs_group_context_slots (
    group_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    creator VARCHAR(255) NOT NULL,
    modifier VARCHAR(255) NOT NULL,
    PRIMARY KEY (group_id, name)
);
