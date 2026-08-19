-- Migration v35: Create campaign tables for cross-execution aggregation (MySQL/OceanBase)
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
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active|paused|completed|failed|abandoned',
  budget_max_tokens BIGINT COMMENT 'Max total tokens across all flows in campaign',
  budget_max_flows INTEGER COMMENT 'Max total flow runs in campaign',
  budget_max_iterations INTEGER COMMENT 'Max total Goal-Loop iterations across all flows',
  used_tokens BIGINT NOT NULL DEFAULT 0 COMMENT 'Tokens consumed so far (aggregated)',
  used_iterations INTEGER NOT NULL DEFAULT 0 COMMENT 'Iterations consumed so far',
  flow_count INTEGER NOT NULL DEFAULT 0 COMMENT 'Number of flow runs associated',
  created_at BIGINT NOT NULL COMMENT 'Unix timestamp (seconds)',
  updated_at BIGINT NOT NULL COMMENT 'Unix timestamp (seconds)',
  completed_at BIGINT COMMENT 'Unix timestamp when campaign reached terminal state',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  INDEX idx_campaigns_status (status)
) COMMENT = 'Campaign: cross-execution aggregation of flow runs sharing a goal';

-- ── campaign_flows ──

CREATE TABLE IF NOT EXISTS campaign_flows (
  id BIGINT NOT NULL AUTO_INCREMENT,
  campaign_id VARCHAR(255) NOT NULL COMMENT 'FK to campaigns.id',
  flow_id VARCHAR(255) NOT NULL COMMENT 'FK to flow_runs.flow_id',
  workflow_id VARCHAR(255) NOT NULL COMMENT 'Workflow definition ID',
  status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running|succeeded|failed|blocked',
  token_usage BIGINT NOT NULL DEFAULT 0 COMMENT 'Token consumption of this flow run',
  started_at BIGINT NOT NULL COMMENT 'Unix timestamp (seconds)',
  completed_at BIGINT COMMENT 'Unix timestamp when flow completed',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_campaign_flows_flow (flow_id),
  INDEX idx_campaign_flows_campaign (campaign_id)
) COMMENT = 'Association between campaigns and individual flow runs';

-- ── campaign_evidence ──

CREATE TABLE IF NOT EXISTS campaign_evidence (
  id BIGINT NOT NULL AUTO_INCREMENT,
  campaign_id VARCHAR(255) NOT NULL COMMENT 'FK to campaigns.id',
  flow_id VARCHAR(255) NOT NULL COMMENT 'Flow that produced this evidence',
  node_id VARCHAR(255) NOT NULL COMMENT 'Node that produced this evidence',
  summary TEXT NOT NULL COMMENT 'Output summary (max 500 chars, truncated)',
  created_at BIGINT NOT NULL COMMENT 'Unix timestamp (seconds)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  INDEX idx_campaign_ev_campaign (campaign_id),
  INDEX idx_campaign_ev_flow (flow_id)
) COMMENT = 'Cross-execution evidence chain: key outputs from multiple flows, ordered by time';

-- ── campaign_gates ──

CREATE TABLE IF NOT EXISTS campaign_gates (
  id VARCHAR(255) NOT NULL COMMENT 'Gate UUID',
  campaign_id VARCHAR(255) NOT NULL COMMENT 'FK to campaigns.id',
  flow_id VARCHAR(255) NOT NULL COMMENT 'Flow containing the human-wait node',
  node_id VARCHAR(255) NOT NULL COMMENT 'Node ID of the human-wait node',
  prompt TEXT NOT NULL COMMENT 'Question shown to the human approver',
  options_json TEXT COMMENT 'JSON array of approval options (e.g. ["approve","reject"])',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending|approved|rejected|expired',
  reason TEXT COMMENT 'Reason for approval/rejection',
  resolved_by VARCHAR(255) COMMENT 'User ID who resolved the gate',
  created_at BIGINT NOT NULL COMMENT 'Unix timestamp (seconds)',
  resolved_at BIGINT COMMENT 'Unix timestamp when gate was resolved',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  INDEX idx_campaign_gates_campaign (campaign_id),
  INDEX idx_campaign_gates_status (status)
) COMMENT = 'Persistent human-approval gates: survive session close, resolved via ClawWeb InterventionPanel';
