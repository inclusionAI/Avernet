-- Apply before deploying the membership producer. No ALTER of ac_space/member.
CREATE TABLE IF NOT EXISTS ac_space_knowledge_sync (
  env VARCHAR(20) NOT NULL,
  space_id BIGINT NOT NULL,
  app VARCHAR(32) NOT NULL,
  revision BIGINT NOT NULL DEFAULT 0,
  synced_revision BIGINT NOT NULL DEFAULT 0,
  snapshot_hash VARCHAR(64) NOT NULL DEFAULT '',
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (env, space_id),
  KEY idx_space_sync_owner (env, app, space_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- Unacknowledged task history is recovery data; do not purge it.
CREATE INDEX idx_task_queue_sync_history
  ON ac_task_queue (env, app, task_type, idempotency_key, id);
