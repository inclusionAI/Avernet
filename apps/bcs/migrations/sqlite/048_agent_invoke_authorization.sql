-- Agent-to-Agent invocation lifecycle metadata. No existing rows or historical
-- migrations are rewritten. A namespace is physically exclusive within an env
-- because legacy edge_grants/permission_requests have no tenant key.
CREATE TABLE agent_invoke_scopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT COLLATE BINARY NOT NULL,
    env TEXT COLLATE BINARY NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    max_out_degree BIGINT NOT NULL CHECK (max_out_degree > 0),
    max_in_degree BIGINT NOT NULL CHECK (max_in_degree > 0),
    max_pending_out BIGINT NOT NULL CHECK (max_pending_out > 0),
    max_pending_in BIGINT NOT NULL CHECK (max_pending_in > 0),
    max_fact_ttl_ms BIGINT NOT NULL CHECK (max_fact_ttl_ms > 0),
    writer_enabled INTEGER NOT NULL DEFAULT 0 CHECK (writer_enabled IN (0, 1)),
    runtime_enabled INTEGER NOT NULL DEFAULT 0 CHECK (runtime_enabled IN (0, 1)),
    UNIQUE (namespace, env),
    UNIQUE (env)
);

-- Published only through the trusted host fact boundary, never an HTTP body.
-- Missing/expired facts fail closed. Refresh cannot reset a generation. A
-- changed/expired qualification requires a new generation before restoration.
CREATE TABLE agent_invoke_facts (
    scope_id BIGINT NOT NULL,
    bot_id TEXT COLLATE BINARY NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    binding_generation BIGINT NOT NULL CHECK (binding_generation > 0),
    eligibility_generation BIGINT NOT NULL CHECK (eligibility_generation > 0),
    eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    hidden INTEGER NOT NULL CHECK (hidden IN (0, 1)),
    valid_until_ms BIGINT NOT NULL CHECK (valid_until_ms > 0),
    PRIMARY KEY (scope_id, bot_id)
);
CREATE INDEX idx_agent_invoke_fact_expiry ON agent_invoke_facts (scope_id, valid_until_ms, bot_id);

-- Trusted governance delegations are exact principal/pair/action facts.
-- Hash + full identity residual keeps the composite key below 3072 bytes.
CREATE TABLE agent_invoke_governance (
    scope_id BIGINT NOT NULL,
    principal_hash TEXT COLLATE BINARY NOT NULL,
    principal_id TEXT COLLATE BINARY NOT NULL,
    from_id TEXT COLLATE BINARY NOT NULL,
    to_id TEXT COLLATE BINARY NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    actions_json TEXT NOT NULL,
    updated_at_ms BIGINT NOT NULL,
    PRIMARY KEY (scope_id, principal_hash, from_id, to_id)
);

-- Approved/revoked edges stay in edge_grants with the existing five-column
-- unique key. These typed columns bind an approval to exact current identities.
CREATE TABLE agent_invoke_grants (
    edge_id BIGINT NOT NULL PRIMARY KEY,
    scope_id BIGINT NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    from_binding_generation BIGINT NOT NULL CHECK (from_binding_generation > 0),
    to_binding_generation BIGINT NOT NULL CHECK (to_binding_generation > 0),
    from_eligibility_generation BIGINT NOT NULL CHECK (from_eligibility_generation > 0),
    to_eligibility_generation BIGINT NOT NULL CHECK (to_eligibility_generation > 0),
    created_at_ms BIGINT NOT NULL,
    updated_at_ms BIGINT NOT NULL
);
CREATE INDEX idx_agent_invoke_grant_scope ON agent_invoke_grants (scope_id, edge_id);

-- Additional metadata belongs here, not in message/rules/decision_reason.
CREATE TABLE agent_invoke_requests (
    request_id TEXT COLLATE BINARY NOT NULL PRIMARY KEY,
    scope_id BIGINT NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    creator_kind TEXT COLLATE BINARY NOT NULL,
    source_ownership_version BIGINT NOT NULL,
    source_authority_sequence BIGINT NOT NULL,
    edge_version BIGINT NOT NULL CHECK (edge_version >= 0),
    from_binding_generation BIGINT NOT NULL CHECK (from_binding_generation > 0),
    to_binding_generation BIGINT NOT NULL CHECK (to_binding_generation > 0),
    from_eligibility_generation BIGINT NOT NULL CHECK (from_eligibility_generation > 0),
    to_eligibility_generation BIGINT NOT NULL CHECK (to_eligibility_generation > 0)
);
CREATE INDEX idx_agent_invoke_request_scope ON agent_invoke_requests (scope_id, request_id);

-- Consumed/released in the same transaction as the pending request terminal.
-- This portable exact-pair uniqueness does not need a nullable generated key.
CREATE TABLE agent_invoke_pending_pairs (
    scope_id BIGINT NOT NULL,
    from_id TEXT COLLATE BINARY NOT NULL,
    to_id TEXT COLLATE BINARY NOT NULL,
    request_id TEXT COLLATE BINARY NOT NULL,
    PRIMARY KEY (scope_id, from_id, to_id),
    UNIQUE (scope_id, request_id)
);

-- Principal hashes bound index bytes. Every hit also verifies full principal
-- kind/id and the canonical command, so a collision never authorizes a replay.
CREATE TABLE agent_invoke_receipts (
    scope_id BIGINT NOT NULL,
    principal_hash TEXT COLLATE BINARY NOT NULL,
    idempotency_key BLOB NOT NULL,
    principal_kind TEXT COLLATE BINARY NOT NULL,
    principal_id TEXT COLLATE BINARY NOT NULL,
    command_json TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    action_id TEXT COLLATE BINARY NOT NULL,
    PRIMARY KEY (scope_id, principal_hash, idempotency_key),
    UNIQUE (scope_id, action_id)
);

CREATE TABLE agent_invoke_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id BIGINT NOT NULL,
    action_id TEXT COLLATE BINARY NOT NULL,
    action TEXT COLLATE BINARY NOT NULL,
    principal_kind TEXT COLLATE BINARY NOT NULL,
    principal_id TEXT COLLATE BINARY NOT NULL,
    from_id TEXT COLLATE BINARY NOT NULL,
    to_id TEXT COLLATE BINARY NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at_ms BIGINT NOT NULL,
    UNIQUE (scope_id, action_id)
);
CREATE INDEX idx_agent_invoke_audit_pair ON agent_invoke_audit (scope_id, from_id, to_id, id);

-- Exact, bounded outbound/inbound keyset and aggregate access paths. Maximum
-- MySQL index width here is 2120 bytes (the scoped governance primary key),
-- below the existing 3072-byte InnoDB index budget. No prefix truncation.
CREATE INDEX idx_agent_invoke_edge_out ON edge_grants (env, from_id, grant_kind, status, id);
CREATE INDEX idx_agent_invoke_edge_in ON edge_grants (env, to_id, grant_kind, status, id);
CREATE INDEX idx_agent_invoke_request_out ON permission_requests (env, from_id, request_kind, status, id);
CREATE INDEX idx_agent_invoke_request_in ON permission_requests (env, to_id, request_kind, status, id);
-- Strict requester-authority epochs are read while the Bot rows are locked.
CREATE INDEX idx_agent_invoke_authority_epoch ON bot_authority_changes (env, bot_id, id);
