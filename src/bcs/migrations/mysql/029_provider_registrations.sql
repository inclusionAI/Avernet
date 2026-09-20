-- VARBINARY preserves case and trailing bytes across all server collations.
-- Provider and ref are bounded to 128 ASCII bytes by the core contract.
CREATE TABLE IF NOT EXISTS bcs_provider_registrations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    env VARBINARY(128) NOT NULL,
    provider_id VARBINARY(128) NOT NULL,
    provider_bot_ref VARBINARY(128) NOT NULL,
    bot_uuid VARBINARY(256) NOT NULL,
    record_json LONGTEXT NOT NULL,
    completed TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_registration_ref_env (env, provider_id, provider_bot_ref),
    UNIQUE KEY uk_registration_bot_env (env, bot_uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
