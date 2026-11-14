import type { Migration } from './schema-types.js';

export const workflowAiDraftMigrations: readonly Migration[] = [{
  version: 153,
  description: 'Persist isolated Editor YAML AI drafts and principal-scoped idempotency',
  sql: [`CREATE TABLE IF NOT EXISTS workflow_ai_drafts (
  draft_id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'Server generated draft UUID',
  request_key VARCHAR(64) NOT NULL COMMENT 'SHA256 of exact workflow actor and request UUID',
  request_digest VARCHAR(64) NOT NULL COMMENT 'SHA256 of exact browser input',
  workflow_id VARCHAR(128) NOT NULL COMMENT 'DB bound workflow identity',
  actor_id VARCHAR(128) NOT NULL COMMENT 'Verified submitting principal',
  envelope_json MEDIUMTEXT NOT NULL COMMENT 'Immutable server-selected AutoHeal Bot and exact editor overlay',
  status VARCHAR(32) NOT NULL COMMENT 'pending running dispatch_uncertain completed failed cancelled',
  job_id VARCHAR(255) COMMENT 'BaaS message identity when acknowledged',
  error_code VARCHAR(64) COMMENT 'Sanitized public diagnostic code',
  result_json MEDIUMTEXT COMMENT 'Validated complete YAML and summary only',
  state_version INTEGER NOT NULL DEFAULT 0 COMMENT 'Compare and swap fence',
  created_at_ms BIGINT NOT NULL COMMENT 'Creation Unix milliseconds',
  updated_at_ms BIGINT NOT NULL COMMENT 'Last state write Unix milliseconds',
  next_poll_at_ms BIGINT NOT NULL COMMENT 'Due time for bounded recovery polling',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Creation timestamp',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Modification timestamp',
  UNIQUE INDEX uk_workflow_ai_draft_request (request_key),
  INDEX idx_workflow_ai_draft_pending (status, next_poll_at_ms)
) COMMENT='Isolated Editor YAML suggestions without save or deploy authority'`],
}];
