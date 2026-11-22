-- Apply before deploying the matching Backend on operator-managed databases.
-- Successful or explicitly rejected requests leave no rows.
CREATE TABLE IF NOT EXISTS ac_local_skill_package_dispatch (
  target_key VARCHAR(64) PRIMARY KEY,
  dispatch_id VARCHAR(36) NOT NULL,
  avernet_tenant VARCHAR(64) NOT NULL,
  env VARCHAR(20) NOT NULL,
  entity_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(100) NOT NULL,
  binding_id INTEGER NOT NULL,
  name VARCHAR(255) NOT NULL,
  content_digest VARCHAR(71) NOT NULL,
  gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_local_package_dispatch_bot
  ON ac_local_skill_package_dispatch (avernet_tenant, env, entity_id, bot_id);
