-- ============================================================================
-- Dev-Workflow Enhancement — Full DDL for MySQL/ZDAS
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
-- ============================================================================


-- ═══════════════════════════════════════════════════════════════════════
-- 1. dev_git_ops
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_git_ops (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  phase_id VARCHAR(64) NOT NULL COMMENT '阶段ID',
  operation VARCHAR(32) NOT NULL COMMENT 'Git操作类型 clone|pull|checkout|commit|push',
  repo_url VARCHAR(1024) NOT NULL COMMENT '仓库URL',
  branch VARCHAR(512) NOT NULL COMMENT '分支名称',
  commit_sha VARCHAR(64) COMMENT '提交SHA',
  commit_message VARCHAR(1024) COMMENT '提交消息',
  remote_branch VARCHAR(512) COMMENT '远程分支名称(可能与本地不同)',
  summary TEXT COMMENT 'AI生成的变更摘要(原dev_code_changes.summary)',
  result VARCHAR(16) NOT NULL DEFAULT 'success' COMMENT '执行结果 success|failed|timeout',
  error_message TEXT COMMENT '错误信息(failed/timeout时)',
  executed_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '执行者(通常是BOT ID)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  CONSTRAINT chk_git_ops_operation CHECK (operation IN ('clone', 'pull', 'checkout', 'commit', 'push')),
  CONSTRAINT chk_git_ops_result CHECK (result IN ('success', 'failed', 'timeout')),
  INDEX idx_dev_git_ops_workflow (workflow_id),
  INDEX idx_dev_git_ops_wf_phase (workflow_id, phase_id),
  INDEX idx_dev_git_ops_commit (commit_sha)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 2. dev_artifacts
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_artifacts (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  phase_id VARCHAR(64) NOT NULL COMMENT '阶段ID',
  artifact_type VARCHAR(64) NOT NULL COMMENT '产物类型 prd|architecture|tech_doc|api_spec|review_report',
  version INTEGER NOT NULL DEFAULT 1 COMMENT '版本号(同一wf+phase+type自动递增)',
  title VARCHAR(512) NOT NULL COMMENT '产物标题',
  content MEDIUMTEXT COMMENT '产物内容(内联存储)',
  content_url VARCHAR(2048) COMMENT '产物外部链接(YuQue等)',
  format VARCHAR(32) NOT NULL DEFAULT 'markdown' COMMENT '内容格式 markdown|yaml|json|html',
  status VARCHAR(32) NOT NULL DEFAULT 'current' COMMENT '状态 draft|current|archived',
  source VARCHAR(32) NOT NULL DEFAULT 'bot' COMMENT '来源 bot|human|imported',
  authored_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '作者(通常是BOT ID或用户ID)',
  archived_at TIMESTAMP NULL COMMENT '归档时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  CONSTRAINT chk_artifact_status CHECK (status IN ('draft', 'current', 'archived')),
  CONSTRAINT chk_artifact_source CHECK (source IN ('bot', 'human', 'imported')),
  CONSTRAINT chk_artifact_format CHECK (format IN ('markdown', 'yaml', 'json', 'html')),
  INDEX idx_dev_artifacts_workflow (workflow_id),
  INDEX idx_dev_artifacts_wf_phase (workflow_id, phase_id),
  INDEX idx_dev_artifacts_type_status (workflow_id, artifact_type, status),
  UNIQUE INDEX uk_dev_artifacts_wf_phase_type_ver (workflow_id, phase_id, artifact_type, version)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 3. dev_phase_conversations
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_phase_conversations (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  phase_id VARCHAR(64) NOT NULL COMMENT '阶段ID',
  baas_message_id VARCHAR(255) COMMENT 'BaaS消息唯一ID(幂等去重键)',
  role VARCHAR(16) NOT NULL COMMENT '角色 user|assistant|system',
  sender_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '发送者ID',
  sender_name VARCHAR(255) COMMENT '发送者显示名称',
  content MEDIUMTEXT NOT NULL COMMENT '消息内容',
  session_id VARCHAR(255) COMMENT 'BaaS会话ID',
  bot_id VARCHAR(255) COMMENT 'BOT ID',
  metadata_json TEXT COMMENT '元数据JSON(扩展字段)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  CONSTRAINT chk_conv_role CHECK (role IN ('user', 'assistant', 'system')),
  INDEX idx_dev_phase_conv_workflow (workflow_id),
  INDEX idx_dev_phase_conv_wf_phase (workflow_id, phase_id),
  UNIQUE INDEX uk_dev_phase_conv_baas_msg (baas_message_id),
  INDEX idx_dev_phase_conv_session (session_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 4. dev_approvals
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_approvals (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  approval_id VARCHAR(255) NOT NULL COMMENT '审批单唯一ID(格式 apr-{uuid})',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  phase_id VARCHAR(64) NOT NULL COMMENT '阶段ID',
  target_type VARCHAR(32) NOT NULL COMMENT '审批对象类型 artifact|code_change|phase',
  target_id VARCHAR(255) NOT NULL COMMENT '审批对象ID',
  title VARCHAR(512) NOT NULL COMMENT '审批标题',
  description TEXT COMMENT '审批描述',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态 pending|approved|rejected|cancelled|expired',
  block_phase INTEGER NOT NULL DEFAULT 0 COMMENT '是否阻塞阶段 1=是 0=否',
  version INTEGER NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  created_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '创建者ID',
  resolved_at TIMESTAMP NULL COMMENT '审批解决时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  CONSTRAINT chk_approval_status CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')),
  CONSTRAINT chk_approval_target CHECK (target_type IN ('artifact', 'code_change', 'phase')),
  UNIQUE INDEX uk_dev_approvals_approval_id (approval_id),
  INDEX idx_dev_approvals_workflow (workflow_id),
  INDEX idx_dev_approvals_wf_phase (workflow_id, phase_id),
  INDEX idx_dev_approvals_target (target_type, target_id),
  INDEX idx_dev_approvals_status (status),
  INDEX idx_dev_approvals_created_by (created_by)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 5. dev_approval_reviewers (includes decision columns — merged from dev_approval_decisions)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_approval_reviewers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  approval_id VARCHAR(255) NOT NULL COMMENT '审批单ID(关联dev_approvals.approval_id)',
  reviewer_id VARCHAR(128) NOT NULL COMMENT '审批人ID',
  reviewer_name VARCHAR(255) COMMENT '审批人显示名称',
  notified INTEGER NOT NULL DEFAULT 0 COMMENT '是否已通知 1=是 0=否',
  decision VARCHAR(16) DEFAULT NULL COMMENT '决策 approved|rejected|commented (NULL=未决策)',
  comment TEXT DEFAULT NULL COMMENT '决策备注',
  decided_at TIMESTAMP NULL DEFAULT NULL COMMENT '决策时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  CONSTRAINT chk_reviewer_decision CHECK (decision IS NULL OR decision IN ('approved', 'rejected', 'commented')),
  UNIQUE INDEX uk_dev_approval_rev_approval_user (approval_id, reviewer_id),
  INDEX idx_dev_approval_rev_reviewer (reviewer_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 6. dev_discussions
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_discussions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  discussion_id VARCHAR(255) NOT NULL COMMENT '讨论唯一ID(格式 disc-{uuid})',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  phase_id VARCHAR(64) NOT NULL COMMENT '阶段ID',
  topic VARCHAR(512) NOT NULL COMMENT '讨论主题',
  description TEXT COMMENT '讨论描述',
  context_type VARCHAR(32) COMMENT '关联上下文类型 artifact|code_change|phase',
  context_id VARCHAR(255) COMMENT '关联上下文ID',
  status VARCHAR(32) NOT NULL DEFAULT 'open' COMMENT '状态 open|closed',
  conclusion TEXT COMMENT '讨论结论(closed时填写)',
  created_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '创建者ID',
  closed_by VARCHAR(128) COMMENT '关闭者ID',
  closed_at TIMESTAMP NULL COMMENT '关闭时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  CONSTRAINT chk_discussion_status CHECK (status IN ('open', 'closed')),
  CONSTRAINT chk_discussion_context CHECK (context_type IS NULL OR context_type IN ('artifact', 'code_change', 'phase')),
  UNIQUE INDEX uk_dev_discussions_disc_id (discussion_id),
  INDEX idx_dev_discussions_workflow (workflow_id),
  INDEX idx_dev_discussions_wf_phase (workflow_id, phase_id),
  INDEX idx_dev_discussions_status (status)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 7. dev_discussion_replies
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_discussion_replies (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  discussion_id VARCHAR(255) NOT NULL COMMENT '讨论ID(关联dev_discussions.discussion_id)',
  author_id VARCHAR(128) NOT NULL COMMENT '回复者ID',
  author_name VARCHAR(255) COMMENT '回复者显示名称',
  content MEDIUMTEXT NOT NULL COMMENT '回复内容',
  parent_reply_id BIGINT COMMENT '父回复ID(支持嵌套回复)',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_dev_disc_rep_disc (discussion_id),
  INDEX idx_dev_disc_rep_author (author_id),
  INDEX idx_dev_disc_rep_parent (parent_reply_id)
);


-- ═══════════════════════════════════════════════════════════════════════
-- 8. dev_project_constraints
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dev_project_constraints (
  id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
  workflow_id VARCHAR(255) NOT NULL COMMENT '工作流ID',
  version INTEGER NOT NULL DEFAULT 1 COMMENT '版本号(同一workflow自动递增)',
  constraints_json MEDIUMTEXT NOT NULL COMMENT '约束内容JSON(技术栈/规范/限制等)',
  change_summary VARCHAR(512) COMMENT '本次变更摘要',
  changed_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '变更者ID',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  INDEX idx_dev_proj_constraints_workflow (workflow_id),
  UNIQUE INDEX uk_dev_proj_constraints_wf_ver (workflow_id, version)
);