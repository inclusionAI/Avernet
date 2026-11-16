import type { Migration } from './schema-types.js';

/** Shared with ClawMind. Nullable defaults keep old writers and explicit NULL valid. */
export const flowRunMarkersDdl = [
  "ALTER TABLE flow_runs ADD COLUMN env VARCHAR(32) NULL DEFAULT 'prod' COMMENT 'Run environment label; historical default is not provenance'",
  "ALTER TABLE flow_runs ADD COLUMN tag VARCHAR(255) NULL DEFAULT '' COMMENT 'Caller-supplied run tag'",
];

export const flowRunMarkersMigrations: readonly Migration[] = [{
  // db.ts also assembles snapshot/request migrations through v151, beyond schema.ts's v144 tail.
  version: 152,
  description: 'Add nullable environment and tag markers to flow runs',
  sql: [],
  async migrate(db) {
    for (const ddl of flowRunMarkersDdl) {
      // MySQL's generic renderer narrows VARCHAR(255) for legacy indexed columns.
      // These unindexed marker columns must keep the declared 255-character width.
      const sql = db.dbType === 'sqlite' ? db.dialect.renderDdl(ddl) : ddl;
      try { await db.exec(sql); }
      catch (error) {
        // Same duplicate-column compatibility as snapshot migrations; nothing else is a no-op.
        if (!/duplicate column name/i.test(String(error))) throw error;
      }
    }
  },
}];
