-- Apply before deploying the membership producer. This is the ONLY new table.
-- One immutable full snapshot per revision; acknowledgement is delivery state.
-- No changes to ac_space, ac_space_member, or ac_task_queue.
-- Keep all unacknowledged revisions and the latest row for each space.
CREATE TABLE IF NOT EXISTS ac_space_knowledge_sync (
  env VARCHAR(20) NOT NULL,
  space_id BIGINT NOT NULL,
  revision BIGINT NOT NULL,
  app VARCHAR(32) NOT NULL,
  payload MEDIUMTEXT NOT NULL,
  snapshot_hash VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  acknowledged TINYINT(1) NOT NULL DEFAULT 0,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (env, space_id, revision),
  KEY idx_space_sync_pending (env, app, acknowledged, space_id, revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
