-- Directed pair histories and pair/action time keysets. Historical DDL is frozen.
CREATE INDEX idx_agent_invoke_request_pair ON permission_requests (env, from_id, to_id, request_kind, status, id);
CREATE INDEX idx_agent_invoke_audit_time ON agent_invoke_audit (scope_id, from_id, to_id, created_at_ms, id);
CREATE INDEX idx_agent_invoke_audit_action_time ON agent_invoke_audit (scope_id, from_id, to_id, action, created_at_ms, id);
