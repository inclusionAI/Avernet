/**
 * Frozen SQLite migration fixtures; no Git or TS evaluation at test runtime.
 *
 * Source: src/evolverun/clawweb/public/shared/server/schema.ts
 * Feature: d27b6772d3158223f8b4876c587f8530f70c7f33
 * Upstream: 7a25bec0f40ffa1e0ae54538cc3934340d9cdb7b
 *   (origin/dev when captured on 2026-09-14).
 * Retrieved with git show <SHA>:<source>; never derived from current schema.
 *
 * The <=119 baseline is deliberately simulated: only workflow_specs DDL from
 * old v5, v32 and v103 is needed by the post-119 migrations. Old v5 already
 * uses gmt_create/gmt_modified, so v7's legacy column rebuild is unnecessary.
 * v119 below is an explicit watermark, not a claim to replay unrelated tables.
 * Fresh-install coverage separately exercises the full production migration list.
 *
 * Historical changes are preserved verbatim with their ORIGINAL numbers:
 * upstream 120 = workflow owner; feature 120 = Stage/Skill tables,
 * feature 121 = integration-test status, 122 = description/development,
 * feature 123 = audit. Do not renumber these to match the merged migrations.
 */
export type HistoricalMigration = {
  version: number;
  description: string;
  sql: string[];
};

const baseline: HistoricalMigration[] = [
  {
    version: 5,
    description: "ClawWeb: workflow_specs for browser-persisted edits",
    sql: [
      `CREATE TABLE IF NOT EXISTS workflow_specs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id VARCHAR(255) NOT NULL,
  pack_id VARCHAR(255),
  spec_json MEDIUMTEXT NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS uk_workflow_specs_workflow_id ON workflow_specs (workflow_id)`,
    ],
  },
  {
    version: 32,
    description: "Add title column to workflow_specs to avoid loading spec_json in list views",
    sql: [
      `ALTER TABLE workflow_specs ADD COLUMN title VARCHAR(255)`,
    ],
  },
  {
    version: 103,
    description: "SQLite: create workflow_deploy_history table with is_active column + add version column to workflow_specs (MySQL got these in v64+v102)",
    sql: [
      `ALTER TABLE workflow_specs ADD COLUMN version INTEGER`,
      `CREATE INDEX IF NOT EXISTS idx_wfs_workflow_version ON workflow_specs (workflow_id, version)`,
    ],
  },
  { version: 119, description: "Simulated pre-merge v119 baseline (affected tables only)", sql: [] },
];
export const feature: HistoricalMigration[] = [
  ...baseline,
  {
    version: 120,
    description: "Add Stage Skill extensions, HITL audit, and registered Skill version assets",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_stage_skill_implementations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_skill_id VARCHAR(64) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  version_no INTEGER NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'validated',
  package_ref TEXT NOT NULL,
  package_sha256 VARCHAR(64) NOT NULL,
  static_validation_json TEXT NOT NULL,
  integration_test_task_id VARCHAR(64),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (implementation_id),
  UNIQUE (stage_skill_id, version_no)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_skill_owner ON ce_stage_skill_implementations (owner_user_id, stage_key, extension_mode, stage_skill_id, gmt_create)`,
      `CREATE TABLE IF NOT EXISTS ce_stage_extension_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  implementation_id VARCHAR(64) NOT NULL,
  initial_input_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (step_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_extension_task ON ce_stage_extension_runs (task_id, stage_key, id)`,
      `CREATE TABLE IF NOT EXISTS ce_stage_interactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  interaction_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(64) NOT NULL,
  attempt_no INTEGER NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'waiting',
  request_json TEXT NOT NULL,
  response_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (interaction_id),
  UNIQUE (step_id, attempt_no)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_interaction_task ON ce_stage_interactions (task_id, step_id, status)`,
      `CREATE TABLE IF NOT EXISTS ce_skill_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  current_version_no INTEGER NOT NULL DEFAULT 1,
  current_package_ref TEXT NOT NULL,
  current_package_sha256 VARCHAR(80) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (asset_id),
  UNIQUE (owner_user_id, bot_id, ocb_skill_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_asset_owner ON ce_skill_assets (owner_user_id, bot_id, gmt_create)`,
      `CREATE TABLE IF NOT EXISTS ce_skill_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version_id VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  version_no INTEGER NOT NULL,
  package_ref TEXT NOT NULL,
  package_sha256 VARCHAR(80) NOT NULL,
  source_task_id VARCHAR(64),
  baseline_package_ref TEXT,
  baseline_package_sha256 VARCHAR(80),
  status VARCHAR(32) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (version_id),
  UNIQUE (asset_id, version_no),
  UNIQUE (asset_id, source_task_id)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_version_asset ON ce_skill_versions (asset_id, version_no)`,
    ],
  },
  {
    version: 121,
    description: "Keep Stage Skill registration and integration-test verification as separate states",
    sql: [
      `ALTER TABLE ce_stage_skill_implementations ADD COLUMN integration_test_status VARCHAR(32) NOT NULL DEFAULT 'untested'`,
    ],
  },
  {
    version: 122,
    description: "Persist custom Stage development before package upload",
    sql: [
      `ALTER TABLE ce_skill_assets ADD COLUMN description TEXT`,
      `CREATE TABLE IF NOT EXISTS ce_stage_developments (
  stage_skill_id VARCHAR(64) PRIMARY KEY,
  owner_user_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  flow_key VARCHAR(32) NOT NULL,
  stage_key VARCHAR(32) NOT NULL,
  extension_mode VARCHAR(32) NOT NULL,
  gmt_create INTEGER NOT NULL,
  gmt_modified INTEGER NOT NULL
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_stage_development_owner ON ce_stage_developments (owner_user_id, gmt_modified)`,
    ],
  },
  {
    version: 123,
    description: "Persist operation-time Skill audit records without historical backfill",
    sql: [
      `CREATE TABLE IF NOT EXISTS ce_skill_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(64) NOT NULL,
  asset_id VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(128) NOT NULL,
  bot_id VARCHAR(128) NOT NULL,
  ocb_skill_id VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  description TEXT,
  task_id VARCHAR(64),
  version_id VARCHAR(64),
  version_no INTEGER,
  event_type VARCHAR(32) NOT NULL,
  actor_id VARCHAR(128),
  actor_type VARCHAR(16) NOT NULL,
  result VARCHAR(64) NOT NULL,
  detail_json TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (event_id),
  UNIQUE (idempotency_key)
)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_audit_owner ON ce_skill_audit_events (owner_user_id, gmt_create)`,
      `CREATE INDEX IF NOT EXISTS idx_ce_skill_audit_task ON ce_skill_audit_events (task_id, event_type)`,
    ],
  },
];

export const upstream: HistoricalMigration[] = [
  ...baseline,
  {
    version: 120,
    description: "Add owner_id to workflow_specs so save can persist the current user alongside deploy history",
    sql: [
      `ALTER TABLE workflow_specs ADD COLUMN owner_id VARCHAR(255) DEFAULT NULL`,
      `CREATE INDEX IF NOT EXISTS idx_workflow_specs_owner_id ON workflow_specs (owner_id)`,
    ],
  },
];
