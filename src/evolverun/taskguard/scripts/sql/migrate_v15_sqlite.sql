-- Migration v15: Add approval_cards table for card-web delivery.
-- Target: SQLite (engine.db)
-- This table is shared between ClawMind (writes on send, polls for resolution)
-- and ClawWeb (reads for display, writes on approve/reject actions).

CREATE TABLE IF NOT EXISTS approval_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  workflow_title VARCHAR(255),
  approval_type VARCHAR(255),
  message TEXT,
  card_fields_json TEXT,
  approver_ids TEXT NOT NULL,
  approver_names TEXT,
  approval_policy VARCHAR(50) NOT NULL DEFAULT 'any',
  approved_by TEXT NOT NULL DEFAULT '',
  rejected_by TEXT NOT NULL DEFAULT '',
  status VARCHAR(50) NOT NULL DEFAULT 'pending',
  delivery_mode VARCHAR(50) NOT NULL DEFAULT 'card-web',
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_approval_cards_flow_node ON approval_cards (flow_id, node_id);
CREATE INDEX IF NOT EXISTS idx_approval_cards_status ON approval_cards (status);
CREATE INDEX IF NOT EXISTS idx_approval_cards_created ON approval_cards (created_at);

CREATE TRIGGER IF NOT EXISTS trg_approval_cards_update
AFTER UPDATE ON approval_cards FOR EACH ROW
BEGIN
  UPDATE approval_cards SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;