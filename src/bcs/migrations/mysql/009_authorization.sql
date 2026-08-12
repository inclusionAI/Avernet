CREATE TABLE IF NOT EXISTS bcs_authz_capabilities (
    capability_id VARCHAR(128) NOT NULL,
    bot_id VARCHAR(128) NOT NULL,
    env VARCHAR(32) NOT NULL,
    tool VARCHAR(128) NOT NULL,
    operation VARCHAR(128) DEFAULT NULL,
    specifier_schema TEXT DEFAULT NULL,
    description TEXT DEFAULT NULL,
    source VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    raw_metadata_json JSON DEFAULT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (capability_id)
);
CREATE INDEX idx_authz_capabilities_bot_env_status ON bcs_authz_capabilities(bot_id, env, status);
CREATE INDEX idx_authz_capabilities_env_tool ON bcs_authz_capabilities(env, tool);

CREATE TABLE IF NOT EXISTS bcs_authz_permission_profiles (
    permission_profile_id VARCHAR(128) NOT NULL,
    revision BIGINT NOT NULL,
    bot_id VARCHAR(128) NOT NULL,
    env VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT NULL,
    rules_template JSON NOT NULL,
    digest VARCHAR(128) NOT NULL,
    is_default TINYINT(1) NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    updated_by VARCHAR(128) DEFAULT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (permission_profile_id, revision)
);
CREATE INDEX idx_authz_permission_profiles_bot_env_status ON bcs_authz_permission_profiles(bot_id, env, status, is_default);

CREATE TABLE IF NOT EXISTS bcs_authz_edge_grants (
    edge_id VARCHAR(128) NOT NULL,
    from_id VARCHAR(128) NOT NULL,
    to_id VARCHAR(128) NOT NULL,
    env VARCHAR(32) NOT NULL,
    grant_kind VARCHAR(64) NOT NULL,
    grant_ref_id VARCHAR(128) NOT NULL,
    rules JSON DEFAULT NULL,
    status VARCHAR(32) NOT NULL,
    originator_policy_type VARCHAR(64) NOT NULL,
    originator_policy_data JSON DEFAULT NULL,
    PRIMARY KEY (edge_id)
);
CREATE INDEX idx_authz_edge_grants_lookup ON bcs_authz_edge_grants(from_id, to_id, env, status);
CREATE INDEX idx_authz_edge_grants_rules_ref ON bcs_authz_edge_grants(grant_ref_id, grant_kind);

CREATE TABLE IF NOT EXISTS bcs_authz_permission_requests (
    request_id VARCHAR(128) NOT NULL,
    edge_id VARCHAR(128) DEFAULT NULL,
    env VARCHAR(32) NOT NULL,
    from_id VARCHAR(128) NOT NULL,
    to_id VARCHAR(128) NOT NULL,
    request_kind VARCHAR(64) NOT NULL,
    requested_ref_id VARCHAR(128) DEFAULT NULL,
    requested_rules JSON DEFAULT NULL,
    message TEXT DEFAULT NULL,
    status VARCHAR(32) NOT NULL,
    decision_reason TEXT DEFAULT NULL,
    created_by VARCHAR(128) NOT NULL,
    decided_by VARCHAR(128) DEFAULT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    decided_at BIGINT DEFAULT NULL,
    PRIMARY KEY (request_id)
);
CREATE INDEX idx_authz_permission_requests_to_status ON bcs_authz_permission_requests(to_id, status);
CREATE INDEX idx_authz_permission_requests_env_from ON bcs_authz_permission_requests(env, from_id);
CREATE INDEX idx_authz_permission_requests_edge_id ON bcs_authz_permission_requests(edge_id, created_at);

CREATE TABLE IF NOT EXISTS bcs_authz_decision_logs (
    decision_id VARCHAR(128) NOT NULL,
    env VARCHAR(32) NOT NULL,
    task_id VARCHAR(128) DEFAULT NULL,
    run_id VARCHAR(128) DEFAULT NULL,
    from_id VARCHAR(128) NOT NULL,
    to_id VARCHAR(128) NOT NULL,
    originator VARCHAR(128) DEFAULT NULL,
    context_type VARCHAR(64) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    reason_code VARCHAR(128) NOT NULL,
    grant_refs JSON NOT NULL,
    context_json JSON DEFAULT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY (decision_id)
);
CREATE INDEX idx_authz_decision_logs_lookup ON bcs_authz_decision_logs(env, from_id, to_id, created_at);
