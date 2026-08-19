-- ============================================================================
-- Migration v33: Add cm_app_config table (SQLite)
-- Purpose:
--   Store non-bootstrap ClawMind application configuration as YAML fragments.
--   The local application.yaml is loaded first, then DB config overrides
--   matching sections in memory (files are never modified).
--
--   Each row stores one top-level YAML config section:
--     config_key   - top-level key name (e.g. "execution", "teclaw", "git")
--     config_yaml  - YAML fragment text for that section (no top-level key, no indent)
--     version      - optimistic lock version, incremented on each update
--     enabled      - 0=disabled, 1=enabled (only enabled rows are loaded)
--     updated_by   - user who last modified this config (clawweb UI)
--
--   Note: No trigger — gmt_modified is updated by the application layer.
--
--   Priority: env vars > cm_app_config DB > application.yaml > built-in defaults
-- ============================================================================

CREATE TABLE IF NOT EXISTS cm_app_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_key VARCHAR(64) NOT NULL,
  config_yaml TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  updated_by VARCHAR(255),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_cm_app_config_key ON cm_app_config (config_key);
CREATE INDEX IF NOT EXISTS idx_cm_app_config_enabled ON cm_app_config (enabled);
