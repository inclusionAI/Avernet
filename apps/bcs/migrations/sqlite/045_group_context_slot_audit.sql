ALTER TABLE bcs_group_context_slots RENAME TO bcs_group_context_slots_before_audit;
CREATE TABLE bcs_group_context_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    creator TEXT NOT NULL,
    modifier TEXT NOT NULL,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (group_id, name)
);
INSERT INTO bcs_group_context_slots (group_id, name, description, creator, modifier)
SELECT group_id, name, description, creator, modifier FROM bcs_group_context_slots_before_audit;
DROP TABLE bcs_group_context_slots_before_audit;
