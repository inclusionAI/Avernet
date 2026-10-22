"""Schema v6: operational acknowledgements and ingestion timestamps."""

RUNTIME_SCHEMA = (
    "ALTER TABLE inbox ADD COLUMN received_ms INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE hosting_resolutions (
        binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
        kind TEXT NOT NULL, target_id TEXT NOT NULL, actor_id TEXT NOT NULL,
        evidence TEXT NOT NULL, created_ms INTEGER NOT NULL,
        PRIMARY KEY(binding_id, kind, target_id)
    )""",
)
