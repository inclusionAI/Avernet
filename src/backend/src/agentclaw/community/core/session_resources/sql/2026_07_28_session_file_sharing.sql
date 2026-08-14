-- Session File Sharing v2 resource metadata.
-- Apply before deploying Backend code that reads these columns. Existing rows
-- remain on the legacy Bot Device File Transfer materialization path.

ALTER TABLE ac_session_resource
  ADD COLUMN transfer_api_version VARCHAR(32) NOT NULL DEFAULT 'bot_device_v1'
    COMMENT 'materialization source: bot_device_v1 or session_v2',
  ADD COLUMN session_key_ciphertext TEXT NULL
    COMMENT 'encrypted TeamClaw session key for Session File Sharing v2';
