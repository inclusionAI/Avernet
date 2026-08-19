-- Migration v16: Add flow_control_slots and flow_control_queue tables for flow control (蓄流).
-- Target: SQLite (engine.db)
-- These tables support the three-scope flow control system, scoped per OpenClaw instance:
--   - global: instance-wide concurrent workflow limit
--   - workflow: per-workflow concurrent instance limit (within an instance)
--   - executor: per-executor-type concurrent node limit (within an instance)
-- instance_id = OWNER_ID + "_" + BOT_ID from ~/.credentials (e.g. "103892_20260402_mnpvqm6v"),
-- isolating flow control pools across different OpenClaw instances sharing the same DB.

CREATE TABLE IF NOT EXISTS flow_control_slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id VARCHAR(255) NOT NULL,
  scope_key VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  acquired_at INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_fc_slots_instance_scope_flow_node ON flow_control_slots (instance_id, scope_key, flow_id, node_id);
CREATE INDEX IF NOT EXISTS idx_fc_slots_instance_scope ON flow_control_slots (instance_id, scope_key);

CREATE TRIGGER IF NOT EXISTS trg_flow_control_slots_update
AFTER UPDATE ON flow_control_slots FOR EACH ROW
BEGIN
  UPDATE flow_control_slots SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS flow_control_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id VARCHAR(255) NOT NULL,
  scope_key VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  priority INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  enqueued_at INTEGER NOT NULL DEFAULT (unixepoch()),
  dispatch_after INTEGER,
  expires_at INTEGER,
  payload TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_fc_queue_instance_scope_status ON flow_control_queue (instance_id, scope_key, status, priority, enqueued_at);
CREATE INDEX IF NOT EXISTS idx_fc_queue_expires ON flow_control_queue (expires_at, status);
CREATE INDEX IF NOT EXISTS idx_fc_queue_instance_flow ON flow_control_queue (instance_id, flow_id);

CREATE TRIGGER IF NOT EXISTS trg_flow_control_queue_update
AFTER UPDATE ON flow_control_queue FOR EACH ROW
BEGIN
  UPDATE flow_control_queue SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;