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
