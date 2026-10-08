-- Expand only. Backfill and validate with all legacy writers fenced before
-- selecting bot_connection_mode. Do not rewrite historical migration checksums.
ALTER TABLE bcs_bots
    ADD COLUMN provider_id VARCHAR(256) DEFAULT NULL,
    ADD COLUMN provider_bot_ref VARCHAR(256) DEFAULT NULL,
    ADD COLUMN connection_mode VARCHAR(16) DEFAULT NULL,
    ADD COLUMN webhook_url TEXT DEFAULT NULL,
    ADD UNIQUE KEY uk_bcs_bots_provider_ref_env (env, provider_id, provider_bot_ref);
