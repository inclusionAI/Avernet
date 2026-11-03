OUTBOX_SCHEMA = """CREATE TABLE outbox (
    delivery_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL UNIQUE REFERENCES executions(turn_id),
    binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
    delivery_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN
        ('queued','claimed','sending','awaiting','checking','unknown','sent','accepted','manual','cancelled')),
    claim_id TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
    polls INTEGER NOT NULL DEFAULT 0, due_ms INTEGER NOT NULL,
    lease_until_ms INTEGER NOT NULL DEFAULT 0,
    task_id TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
    remote_conversation_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
    created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL
)"""
