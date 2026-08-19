-- Migration v34: Create run_logs table for console log capture (SQLite)
-- Stores console.log/warn/error output captured during workflow execution,
-- keyed by flow_id for run archive generation.

CREATE TABLE IF NOT EXISTS run_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255),
  level VARCHAR(32) NOT NULL,
  source VARCHAR(255),
  message TEXT NOT NULL,
  timestamp BIGINT NOT NULL,
  seq INTEGER NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_run_logs_flow_id ON run_logs (flow_id);
CREATE INDEX IF NOT EXISTS idx_run_logs_flow_node ON run_logs (flow_id, node_id);
CREATE INDEX IF NOT EXISTS idx_run_logs_level ON run_logs (flow_id, level);

CREATE TRIGGER IF NOT EXISTS trg_run_logs_update AFTER UPDATE ON run_logs FOR EACH ROW
BEGIN
  UPDATE run_logs SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;
