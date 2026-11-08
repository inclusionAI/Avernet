-- Additive catalogue migration. Existing Bot/Skill rows are never rewritten.
CREATE TABLE IF NOT EXISTS ac_experts (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  avernet_tenant VARCHAR(64) NOT NULL,
  env VARCHAR(20) NOT NULL,
  source_repo_id VARCHAR(64) NOT NULL,
  expert_id VARCHAR(64) NOT NULL,
  name VARCHAR(40) NOT NULL,
  description VARCHAR(200) NOT NULL,
  category VARCHAR(40) NOT NULL,
  tags TEXT NOT NULL,
  enabled BOOLEAN NOT NULL,
  is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
  first_added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  source_commit VARCHAR(40) NOT NULL,
  expert_path VARCHAR(256) NOT NULL,
  content_digest VARCHAR(64) NOT NULL,
  effective_input LONGTEXT NOT NULL,
  last_seen_commit VARCHAR(40) NOT NULL,
  sync_error TEXT NULL,
  CONSTRAINT uk_expert_scope UNIQUE (avernet_tenant, env, source_repo_id, expert_id),
  INDEX idx_expert_discovery (avernet_tenant, env, source_repo_id, enabled, is_deleted)
);
