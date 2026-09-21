-- Expand only. NULL connection_mode denotes a record not yet backfilled.
-- Keep legacy gateway bindings as the compatibility delivery projection.
ALTER TABLE bcs_bots ADD COLUMN provider_id TEXT COLLATE BINARY DEFAULT NULL;
ALTER TABLE bcs_bots ADD COLUMN provider_bot_ref TEXT COLLATE BINARY DEFAULT NULL;
ALTER TABLE bcs_bots ADD COLUMN connection_mode TEXT DEFAULT NULL CHECK (connection_mode IN ('plugin', 'gateway'));
ALTER TABLE bcs_bots ADD COLUMN webhook_url TEXT DEFAULT NULL;
CREATE UNIQUE INDEX uk_bcs_bots_provider_ref_env ON bcs_bots (env, provider_id, provider_bot_ref);
