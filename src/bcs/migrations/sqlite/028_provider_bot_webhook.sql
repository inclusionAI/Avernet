-- Applied exactly once by the versioned SQLite migration runner.
ALTER TABLE bcs_provider_bot_bindings ADD COLUMN webhook_url TEXT;
