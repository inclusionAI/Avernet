import type { migrations } from './schema.js';
const indexes = [
  { name: 'idx_repair_history_time', columns: 'task_id, record_kind, created_at_ms, record_id' },
  { name: 'idx_repair_history_status', columns: 'task_id, record_kind, record_status, created_at_ms, record_id' },
];
/** Indexed display projection; JSON remains authoritative and old rows need no backfill writes. */
export const automationHistoryMigration: (typeof migrations)[number] = {
  version: 146,
  description: 'Index workflow automation history by kind status and creation time',
  async migrate(db) {
    const status = db.dialect.driver === 'better-sqlite3'
      ? "JSON_EXTRACT(body_json, '$.status')" : "JSON_UNQUOTE(JSON_EXTRACT(body_json, '$.status'))";
    try {
      await db.exec(`ALTER TABLE workflow_repair_release_records ADD COLUMN record_status VARCHAR(16) GENERATED ALWAYS AS (CASE WHEN record_kind IN ('auto_run', 'auto_draft', 'auto_notice') THEN ${status} ELSE NULL END) VIRTUAL`);
    } catch (error) {
      if (!/duplicate column name/i.test(String(error))) throw error;
    }
    // OceanBase/ZDAS deliberately skips standalone CREATE INDEX in renderDdl.
    if (db.dialect.inlineIndexesOnly) for (const index of indexes) {
      try { await db.exec(`ALTER TABLE workflow_repair_release_records ADD INDEX ${index.name} (${index.columns})`); }
      catch (error) { if (!/duplicate key name/i.test(String(error))) throw error; }
    }
  },
  sql: indexes.map(index => `CREATE INDEX IF NOT EXISTS ${index.name} ON workflow_repair_release_records (${index.columns})`),
};
