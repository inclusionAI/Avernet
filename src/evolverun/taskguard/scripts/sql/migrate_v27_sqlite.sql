-- Migration v27: Make http_callback_configs.secret nullable — SQLite
-- Target: SQLite (engine.db)
-- SQLite does not support ALTER TABLE ... MODIFY COLUMN, so we recreate the table.
-- Signing is now optional: when secret is NULL, no signature headers are sent.

ALTER TABLE http_callback_configs RENAME TO http_callback_configs_old;

CREATE TABLE http_callback_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  url VARCHAR(1024) NOT NULL,
  secret VARCHAR(1024),
  enabled INTEGER NOT NULL DEFAULT 1,
  notify_on TEXT NOT NULL,
  timeout_ms INTEGER NOT NULL DEFAULT 5000,
  max_retries INTEGER NOT NULL DEFAULT 2,
  retry_delay_ms INTEGER NOT NULL DEFAULT 1000,
  include_node_output INTEGER NOT NULL DEFAULT 0,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (config_id)
);

INSERT INTO http_callback_configs SELECT * FROM http_callback_configs_old;
DROP TABLE http_callback_configs_old;

CREATE INDEX IF NOT EXISTS idx_http_callback_configs_workflow ON http_callback_configs (workflow_id);