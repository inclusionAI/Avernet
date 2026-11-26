-- Additive migration. Import approved historical definitions before DI cutover.
-- No request-path CREATE/seed, UPDATE/DELETE, latest fallback or runtime recovery.
CREATE TABLE IF NOT EXISTS ac_context_collab_product_profile (
    id BIGINT NOT NULL AUTO_INCREMENT,
    profile_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    profile_version INT NOT NULL,
    definition_schema_version INT NOT NULL,
    definition_json MEDIUMTEXT NOT NULL,
    definition_digest CHAR(64) NOT NULL,
    published_by MEDIUMTEXT NOT NULL,
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
    gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_context_collab_product_profile (avernet_tenant, env, profile_id, profile_version)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
