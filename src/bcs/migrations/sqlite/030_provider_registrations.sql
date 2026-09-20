-- Independent registration journal, never a Provider delivery binding.
CREATE TABLE IF NOT EXISTS bcs_provider_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    env TEXT COLLATE BINARY NOT NULL,
    provider_id TEXT COLLATE BINARY NOT NULL,
    provider_bot_ref TEXT COLLATE BINARY NOT NULL,
    bot_uuid TEXT COLLATE BINARY NOT NULL,
    record_json TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
    UNIQUE (env, provider_id, provider_bot_ref),
    UNIQUE (env, bot_uuid)
);
