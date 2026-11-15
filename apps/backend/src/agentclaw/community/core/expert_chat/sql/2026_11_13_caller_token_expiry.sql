-- MySQL / OceanBase: apply before deploying expiry-aware Caller refresh.
-- NULL means no confirmed expiry for the current instance; do not backfill
-- from ASC expiry, credential issue time, or unconfirmed outbound attempts.
ALTER TABLE ac_expert_chat_instance
    ADD COLUMN caller_token_expires_at DATETIME(6) NULL
    COMMENT 'UTC expiry of the Caller token successfully installed on this instance';
