-- ============================================================================
-- Dev-Workflow Enhancement — Full DDL for SQLite
-- Consolidated from v49–v59, covers 8 tables:
--
--   代码拉取追踪:
--     1. dev_git_ops              — BOT Git操作记录(clone/pull/commit/push) + AI变更摘要
--
--   产物版本管理:
--     2. dev_artifacts            — 阶段产物版本管理(draft→current→archived)
--
--   对话持久化:
--     3. dev_phase_conversations  — 阶段对话消息(from BaaS/本地)
--
--   审批体系 (decisions merged into reviewers):
--     4. dev_approvals            — 审批单
--     5. dev_approval_reviewers   — 审批人 + 决策(decision/comment/decided_at)
--
--   讨论系统 (participants derived from created_by + replies):
--     6. dev_discussions          — 讨论主题
--     7. dev_discussion_replies   — 讨论回复
--
--   项目约束版本化:
--     8. dev_project_constraints  — 项目约束版本历史
--
-- Dropped tables:
--   - dev_code_changes / dev_code_change_files (replaced by Git API queries, summary → dev_git_ops)
--   - dev_approval_decisions (merged into dev_approval_reviewers)
--   - dev_discussion_participants (derived from created_by + reply author_id)
--
-- Note: SQLite does not support COMMENT or ON UPDATE CURRENT_TIMESTAMP.
--       gmt_modified is updated via triggers defined below.
-- ============================================================================


-- ═══════════════════════════════════════════════════════════════════════
-- 1. dev_git_ops
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_git_ops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(64) NOT NULL,
  operation VARCHAR(32) NOT NULL,
  repo_url VARCHAR(1024) NOT NULL,
  branch VARCHAR(512) NOT NULL,
  commit_sha VARCHAR(64),
  commit_message VARCHAR(1024),
  remote_branch VARCHAR(512),
  summary TEXT,
  result VARCHAR(16) NOT NULL DEFAULT 'success',
  error_message TEXT,
  executed_by VARCHAR(128) NOT NULL DEFAULT '',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  CONSTRAINT chk_git_ops_operation CHECK (operation IN ('clone', 'pull', 'checkout', 'commit', 'push')),
  CONSTRAINT chk_git_ops_result CHECK (result IN ('success', 'failed', 'timeout'))
);

CREATE INDEX IF NOT EXISTS idx_dev_git_ops_workflow ON dev_git_ops (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_git_ops_wf_phase ON dev_git_ops (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_git_ops_commit ON dev_git_ops (commit_sha);

CREATE TRIGGER IF NOT EXISTS trg_dev_git_ops_update
AFTER UPDATE ON dev_git_ops FOR EACH ROW
BEGIN
  UPDATE dev_git_ops SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;


-- ═══════════════════════════════════════════════════════════════════════
-- 2. dev_artifacts
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(64) NOT NULL,
  artifact_type VARCHAR(64) NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  title VARCHAR(512) NOT NULL,
  content MEDIUMTEXT,
  content_url VARCHAR(2048),
  format VARCHAR(32) NOT NULL DEFAULT 'markdown',
  status VARCHAR(32) NOT NULL DEFAULT 'current',
  source VARCHAR(32) NOT NULL DEFAULT 'bot',
  authored_by VARCHAR(128) NOT NULL DEFAULT '',
  archived_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  CONSTRAINT chk_artifact_status CHECK (status IN ('draft', 'current', 'archived')),
  CONSTRAINT chk_artifact_source CHECK (source IN ('bot', 'human', 'imported')),
  CONSTRAINT chk_artifact_format CHECK (format IN ('markdown', 'yaml', 'json', 'html'))
);

CREATE INDEX IF NOT EXISTS idx_dev_artifacts_workflow ON dev_artifacts (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_artifacts_wf_phase ON dev_artifacts (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_artifacts_type_status ON dev_artifacts (workflow_id, artifact_type, status);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_artifacts_wf_phase_type_ver ON dev_artifacts (workflow_id, phase_id, artifact_type, version);

CREATE TRIGGER IF NOT EXISTS trg_dev_artifacts_update
AFTER UPDATE ON dev_artifacts FOR EACH ROW
BEGIN
  UPDATE dev_artifacts SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;


-- ═══════════════════════════════════════════════════════════════════════
-- 3. dev_phase_conversations
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_phase_conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(64) NOT NULL,
  baas_message_id VARCHAR(255),
  role VARCHAR(16) NOT NULL,
  sender_id VARCHAR(128) NOT NULL DEFAULT '',
  sender_name VARCHAR(255),
  content MEDIUMTEXT NOT NULL,
  session_id VARCHAR(255),
  bot_id VARCHAR(255),
  metadata_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  CONSTRAINT chk_conv_role CHECK (role IN ('user', 'assistant', 'system'))
);

CREATE INDEX IF NOT EXISTS idx_dev_phase_conv_workflow ON dev_phase_conversations (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_phase_conv_wf_phase ON dev_phase_conversations (workflow_id, phase_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_phase_conv_baas_msg ON dev_phase_conversations (baas_message_id);
CREATE INDEX IF NOT EXISTS idx_dev_phase_conv_session ON dev_phase_conversations (session_id);

CREATE TRIGGER IF NOT EXISTS trg_dev_phase_conversations_update
AFTER UPDATE ON dev_phase_conversations FOR EACH ROW
BEGIN
  UPDATE dev_phase_conversations SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;


-- ═══════════════════════════════════════════════════════════════════════
-- 4. dev_approvals
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(64) NOT NULL,
  target_type VARCHAR(32) NOT NULL,
  target_id VARCHAR(255) NOT NULL,
  title VARCHAR(512) NOT NULL,
  description TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  block_phase INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0,
  created_by VARCHAR(128) NOT NULL DEFAULT '',
  resolved_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  CONSTRAINT chk_approval_status CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')),
  CONSTRAINT chk_approval_target CHECK (target_type IN ('artifact', 'code_change', 'phase'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_approvals_approval_id ON dev_approvals (approval_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_workflow ON dev_approvals (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_wf_phase ON dev_approvals (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_target ON dev_approvals (target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_status ON dev_approvals (status);
CREATE INDEX IF NOT EXISTS idx_dev_approvals_created_by ON dev_approvals (created_by);


-- ═══════════════════════════════════════════════════════════════════════
-- 5. dev_approval_reviewers (includes decision columns — merged from dev_approval_decisions)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_approval_reviewers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id VARCHAR(255) NOT NULL,
  reviewer_id VARCHAR(128) NOT NULL,
  reviewer_name VARCHAR(255),
  notified INTEGER NOT NULL DEFAULT 0,
  decision VARCHAR(16) DEFAULT NULL,
  comment TEXT DEFAULT NULL,
  decided_at INTEGER DEFAULT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  CONSTRAINT chk_reviewer_decision CHECK (decision IS NULL OR decision IN ('approved', 'rejected', 'commented')),
  FOREIGN KEY (approval_id) REFERENCES dev_approvals(approval_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_approval_rev_approval_user ON dev_approval_reviewers (approval_id, reviewer_id);
CREATE INDEX IF NOT EXISTS idx_dev_approval_rev_reviewer ON dev_approval_reviewers (reviewer_id);

CREATE TRIGGER IF NOT EXISTS trg_dev_approvals_update
AFTER UPDATE ON dev_approvals FOR EACH ROW
BEGIN
  UPDATE dev_approvals SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_dev_approval_reviewers_update
AFTER UPDATE ON dev_approval_reviewers FOR EACH ROW
BEGIN
  UPDATE dev_approval_reviewers SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;


-- ═══════════════════════════════════════════════════════════════════════
-- 6. dev_discussions
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_discussions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  discussion_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  phase_id VARCHAR(64) NOT NULL,
  topic VARCHAR(512) NOT NULL,
  description TEXT,
  context_type VARCHAR(32),
  context_id VARCHAR(255),
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  conclusion TEXT,
  created_by VARCHAR(128) NOT NULL DEFAULT '',
  closed_by VARCHAR(128),
  closed_at INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  CONSTRAINT chk_discussion_status CHECK (status IN ('open', 'closed')),
  CONSTRAINT chk_discussion_context CHECK (context_type IS NULL OR context_type IN ('artifact', 'code_change', 'phase'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_discussions_disc_id ON dev_discussions (discussion_id);
CREATE INDEX IF NOT EXISTS idx_dev_discussions_workflow ON dev_discussions (workflow_id);
CREATE INDEX IF NOT EXISTS idx_dev_discussions_wf_phase ON dev_discussions (workflow_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_dev_discussions_status ON dev_discussions (status);


-- ═══════════════════════════════════════════════════════════════════════
-- 7. dev_discussion_replies
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_discussion_replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  discussion_id VARCHAR(255) NOT NULL,
  author_id VARCHAR(128) NOT NULL,
  author_name VARCHAR(255),
  content MEDIUMTEXT NOT NULL,
  parent_reply_id INTEGER,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  FOREIGN KEY (discussion_id) REFERENCES dev_discussions(discussion_id),
  FOREIGN KEY (parent_reply_id) REFERENCES dev_discussion_replies(id)
);

CREATE INDEX IF NOT EXISTS idx_dev_disc_rep_disc ON dev_discussion_replies (discussion_id);
CREATE INDEX IF NOT EXISTS idx_dev_disc_rep_author ON dev_discussion_replies (author_id);
CREATE INDEX IF NOT EXISTS idx_dev_disc_rep_parent ON dev_discussion_replies (parent_reply_id);

CREATE TRIGGER IF NOT EXISTS trg_dev_discussions_update
AFTER UPDATE ON dev_discussions FOR EACH ROW
BEGIN
  UPDATE dev_discussions SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_dev_discussion_replies_update
AFTER UPDATE ON dev_discussion_replies FOR EACH ROW
BEGIN
  UPDATE dev_discussion_replies SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;


-- ═══════════════════════════════════════════════════════════════════════
-- 8. dev_project_constraints
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_project_constraints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  constraints_json MEDIUMTEXT NOT NULL,
  change_summary VARCHAR(512),
  changed_by VARCHAR(128) NOT NULL DEFAULT '',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_dev_proj_constraints_workflow ON dev_project_constraints (workflow_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dev_proj_constraints_wf_ver ON dev_project_constraints (workflow_id, version);

CREATE TRIGGER IF NOT EXISTS trg_dev_project_constraints_update
AFTER UPDATE ON dev_project_constraints FOR EACH ROW
BEGIN
  UPDATE dev_project_constraints SET gmt_modified = (unixepoch()) WHERE id = NEW.id;
END;