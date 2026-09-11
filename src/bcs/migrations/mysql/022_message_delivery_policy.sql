-- Environment-scoped queue business policy. No credentials or host paths.
CREATE TABLE bcs_message_delivery_policy (
    env VARCHAR(64) NOT NULL PRIMARY KEY,
    version BIGINT NOT NULL,
    policy_json LONGTEXT NOT NULL,
    updated_by VARCHAR(256) NOT NULL,
    updated_at_ms BIGINT NOT NULL
);
