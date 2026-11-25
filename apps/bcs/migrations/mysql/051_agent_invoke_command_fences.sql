-- Agent invocation command fencing. Historical migrations are frozen.
-- Zero metadata is unknown/unbound and cannot authorize a new invocation.
ALTER TABLE bcs_bots ADD COLUMN agent_invoke_identity_revision BIGINT NOT NULL DEFAULT 1;
ALTER TABLE agent_invoke_grants ADD COLUMN from_registration_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_grants ADD COLUMN to_registration_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_grants ADD COLUMN from_registration_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_grants ADD COLUMN to_registration_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN from_registration_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN to_registration_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN from_registration_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN to_registration_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN requester_registration_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN requester_registration_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_requests ADD COLUMN requester_delegation_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_facts ADD COLUMN registration_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_invoke_facts ADD COLUMN registration_revision BIGINT NOT NULL DEFAULT 0;
-- Keep from_id/to_id collations and five-part key semantics; widen only.
-- The widened five-part key is 2440 bytes, below the 3072-byte limit.
ALTER TABLE edge_grants MODIFY COLUMN env VARCHAR(64) NOT NULL;
ALTER TABLE permission_requests
    MODIFY COLUMN env VARCHAR(64) NOT NULL,
    MODIFY COLUMN created_by VARCHAR(256) NOT NULL,
    MODIFY COLUMN decided_by VARCHAR(256) DEFAULT NULL;
