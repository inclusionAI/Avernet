-- Migration v26: Make http_callback_configs.secret nullable — MySQL
-- Target: MySQL / ZDAS (OceanBase)
-- Signing is now optional: when secret is NULL, no signature headers are sent.

ALTER TABLE http_callback_configs MODIFY COLUMN secret VARCHAR(1024) NULL COMMENT 'HMAC-SHA256 signing secret (optional)';