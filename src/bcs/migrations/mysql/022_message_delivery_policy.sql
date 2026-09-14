-- Environment-scoped queue business policy. No credentials or host paths.
CREATE TABLE bcs_message_delivery_policy (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    env VARCHAR(64) NOT NULL,
    version BIGINT NOT NULL,
    policy_json LONGTEXT NOT NULL,
    updated_by VARCHAR(256) NOT NULL,
    updated_at_ms BIGINT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_delivery_policy_env (env)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
