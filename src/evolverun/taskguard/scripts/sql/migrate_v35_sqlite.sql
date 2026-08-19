-- Migration v35: Create campaign tables for cross-execution aggregation (SQLite)
-- Campaign is an upper-level aggregation of multiple flow runs sharing a goal.
-- Borrows LoopX control-plane concepts (Goal, Gate, Quota, Evidence) implemented natively.
--
-- Tables:
--   campaigns          — campaign metadata + aggregated budget usage
--   campaign_flows     — association between campaigns and flow runs
--   campaign_evidence  — cross-execution evidence chain (key outputs by time)
--   campaign_gates     — persistent human-approval gates (survive session close)

-- ── campaigns ──

CREATE TABLE IF NOT EXISTS campaigns (
  id VARCHAR(255) NOT NULL,
  goal TEXT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  budget_max_tokens INTEGER,
  budget_max_flows INTEGER,
  budget_max_iterations INTEGER,
  used_tokens INTEGER NOT NULL DEFAULT 0,
  used_iterations INTEGER NOT NULL DEFAULT 0,
  flow_count INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns (status);

CREATE TRIGGER IF NOT EXISTS trg_campaigns_update AFTER UPDATE ON campaigns FOR EACH ROW
BEGIN
  UPDATE campaigns SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── campaign_flows ──

CREATE TABLE IF NOT EXISTS campaign_flows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  token_usage INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER NOT NULL DEFAULT (unixepoch()),
  completed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_campaign_flows_flow ON campaign_flows (flow_id);
CREATE INDEX IF NOT EXISTS idx_campaign_flows_campaign ON campaign_flows (campaign_id);

CREATE TRIGGER IF NOT EXISTS trg_campaign_flows_update AFTER UPDATE ON campaign_flows FOR EACH ROW
BEGIN
  UPDATE campaign_flows SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── campaign_evidence ──

CREATE TABLE IF NOT EXISTS campaign_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  summary TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_campaign_ev_campaign ON campaign_evidence (campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_ev_flow ON campaign_evidence (flow_id);

CREATE TRIGGER IF NOT EXISTS trg_campaign_evidence_update AFTER UPDATE ON campaign_evidence FOR EACH ROW
BEGIN
  UPDATE campaign_evidence SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

-- ── campaign_gates ──

CREATE TABLE IF NOT EXISTS campaign_gates (
  id VARCHAR(255) NOT NULL,
  campaign_id VARCHAR(255) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  prompt TEXT NOT NULL,
  options_json TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  reason TEXT,
  resolved_by VARCHAR(255),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_gates_campaign ON campaign_gates (campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_gates_status ON campaign_gates (status);

CREATE TRIGGER IF NOT EXISTS trg_campaign_gates_update AFTER UPDATE ON campaign_gates FOR EACH ROW
BEGIN
  UPDATE campaign_gates SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;
