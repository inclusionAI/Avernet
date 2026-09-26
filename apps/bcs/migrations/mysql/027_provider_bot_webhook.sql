-- NULL preserves inheritance of the Provider's default downlink endpoint.
ALTER TABLE bcs_provider_bot_bindings ADD COLUMN webhook_url TEXT NULL;
