import type { migrations } from './schema.js';
/** Additive association fields; existing run and retry contracts are unchanged. */
export const workflowSnapshotColumnDdl = [
  "ALTER TABLE flow_runs ADD COLUMN workflow_snapshot_json TEXT DEFAULT NULL COMMENT '工作流运行快照关联'",
  "ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN workflow_snapshot_json TEXT DEFAULT NULL COMMENT '分析来源工作流快照关联'",
];
import { flowRunLinkMysqlUpgradeDdl, assertFlowRunLinkMysqlSchema } from './schema-flow-run-links.js';

export const workflowSnapshotMigrations: typeof migrations = [{
  version: 145,
  description: 'Associate workflow runs and analyses with immutable configuration snapshots',
  sql: [workflowSnapshotColumnDdl[0]],
  async migrate(db) {
    // Some public installations have no analysis feature/table. Do not require it to boot.
    const tables = db.dbType === 'sqlite'
      ? await db.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workflow_evolution_analysis_runs'")
      : await db.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workflow_evolution_analysis_runs'");
    if (!tables.length) return;
    try { await db.exec(db.dialect.renderDdl(workflowSnapshotColumnDdl[1])); }
    catch (error) { if (!/duplicate column name/i.test(String(error))) throw error; }
  },
}, {
  version: 147,
  description: 'Store trusted subworkflow run associations independently',
  sql: [
    `CREATE TABLE IF NOT EXISTS flow_run_links (
  child_flow_id VARCHAR(255) NOT NULL PRIMARY KEY,
  parent_flow_id VARCHAR(255) NOT NULL,
  parent_node_id VARCHAR(255) NOT NULL,
  parent_attempt INTEGER,
  child_workflow_id VARCHAR(255) NOT NULL,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  INDEX idx_flow_run_links_parent (parent_flow_id)
)`,
  ],
}, {
  version: 148,
  description: 'Allow full-length flow run link identifiers',
  sql: [],
  async migrate(db) {
    // SQLite does not enforce VARCHAR length, including on existing v147 tables.
    if (db.dbType === 'mysql' || db.dbType === 'zdas') {
      await db.exec(flowRunLinkMysqlUpgradeDdl);
      await assertFlowRunLinkMysqlSchema(db);
    }
  },
}];
