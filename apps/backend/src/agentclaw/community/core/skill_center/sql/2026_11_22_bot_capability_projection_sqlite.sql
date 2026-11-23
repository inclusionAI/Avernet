-- Deploy before Backend workers that write or reconcile Bot capabilities.
-- Retain this additive table when rolling back Backend or disabling deferral.
CREATE TABLE IF NOT EXISTS ac_bot_capability_projection (
    bot_pk BIGINT NOT NULL PRIMARY KEY,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) NOT NULL,
    binding_id BIGINT NULL,
    device_id VARCHAR(128) NULL,
    engine_type VARCHAR(64) NOT NULL,
    version BIGINT NOT NULL,
    dispatch_version BIGINT NULL,
    dispatch_device_id VARCHAR(128) NULL,
  dispatch_history_json TEXT NULL,
    retry_pending BOOLEAN NOT NULL,
    scope_json TEXT NOT NULL,
    retired_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_capability_projection_recovery
    ON ac_bot_capability_projection (env, retry_pending, bot_pk);
