-- A2A Card registration and normalized remote Task/Run/context associations.
-- New capability only; existing Bot/Provider ownership and history are retained.
ALTER TABLE bcs_bots ADD COLUMN agent_card LONGTEXT DEFAULT NULL;
ALTER TABLE bcs_bots ADD COLUMN agent_card_url TEXT DEFAULT NULL;
ALTER TABLE bcs_bots ADD COLUMN agent_card_revision BIGINT NOT NULL DEFAULT 0;
CREATE INDEX idx_bot_a2a_refresh ON bcs_bots(env,is_deleted,bot_uuid);

CREATE TABLE bcs_a2a_tasks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    env VARCHAR(32) NOT NULL,
    task_key CHAR(64) NOT NULL,
    scope_key CHAR(64) NOT NULL,
    remote_task_id TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    context_id TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    pending_input_json LONGTEXT DEFAULT NULL,
    version BIGINT NOT NULL,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE (env, task_key)
);
CREATE TABLE bcs_a2a_run_bindings (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    env VARCHAR(32) NOT NULL,
    run_id VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    task_key CHAR(64) DEFAULT NULL,
    scope_key CHAR(64) NOT NULL,
    bot_id VARCHAR(255) NOT NULL,
    session_id TEXT NOT NULL,
    provider_id VARCHAR(255) NOT NULL,
    provider_bot_ref VARCHAR(256) NOT NULL,
    interface_url TEXT NOT NULL,
    tenant TEXT DEFAULT NULL,
    route_fingerprint CHAR(64) DEFAULT NULL,
    group_id VARCHAR(255) NOT NULL,
    protocol_version VARCHAR(32) NOT NULL,
    binding VARCHAR(32) NOT NULL,
    update_mode VARCHAR(32) NOT NULL,
    credential_kind VARCHAR(128) NOT NULL,
    extensions_json LONGTEXT NOT NULL,
    message_id VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    request_digest CHAR(64) NOT NULL,
    request_context_id TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL,
    state VARCHAR(32) NOT NULL,
    version BIGINT NOT NULL,
    deadline_ms BIGINT NOT NULL,
    updated_at_ms BIGINT NOT NULL,
    budget_remaining_ms BIGINT DEFAULT NULL,
    budget_total_deadline_ms BIGINT DEFAULT NULL,
    budget_input_wait_ms BIGINT DEFAULT NULL,
    budget_phase VARCHAR(32) DEFAULT NULL,
    budget_phase_started_ms BIGINT DEFAULT NULL,
    budget_phase_deadline_ms BIGINT DEFAULT NULL,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE (env, run_id),
    FOREIGN KEY (env, task_key) REFERENCES bcs_a2a_tasks (env, task_key)
);
CREATE INDEX idx_a2a_active_scope ON bcs_a2a_run_bindings(env, scope_key, state);
CREATE INDEX idx_a2a_task_runs ON bcs_a2a_run_bindings(env, task_key, state, run_id);
CREATE TABLE bcs_a2a_contexts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    env VARCHAR(32) NOT NULL,
    scope_key CHAR(64) NOT NULL,
    bot_id VARCHAR(255) NOT NULL,
    session_id TEXT NOT NULL,
    provider_id VARCHAR(255) NOT NULL,
    provider_bot_ref VARCHAR(256) NOT NULL,
    interface_url TEXT NOT NULL,
    tenant TEXT DEFAULT NULL,
    route_fingerprint CHAR(64) DEFAULT NULL,
    context_id TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE (env, scope_key)
);
