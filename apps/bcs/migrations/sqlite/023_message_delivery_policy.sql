CREATE TABLE IF NOT EXISTS bcs_message_delivery_policy (
    env TEXT NOT NULL PRIMARY KEY,
    version INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
