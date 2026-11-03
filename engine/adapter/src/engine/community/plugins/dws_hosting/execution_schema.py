"""Additive schema v3. A durable dispatch reservation is never automatically replayed."""

EXECUTION_SCHEMA = """CREATE TABLE executions (
    turn_id TEXT PRIMARY KEY REFERENCES batches(batch_id),
    binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
    invocation_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('dispatching','accepted','received','unknown','resolved','manual','cancelled')),
    result_json TEXT, decision_json TEXT, reason TEXT NOT NULL DEFAULT ''
)"""
