import type { Migration } from './schema-types.js';

/** NULL preserves unknown or unconsumed cards; do not infer consumption from audit timestamps. */
export const approvalConsumptionColumnDdl = "ALTER TABLE approval_cards ADD COLUMN processed_at BIGINT DEFAULT NULL COMMENT 'Workflow consumption time in unix seconds; NULL means unconsumed or unknown'";

export const approvalConsumptionIndexDdl = {
  sqlite: 'CREATE INDEX IF NOT EXISTS idx_approval_cards_polling ON approval_cards (delivery_mode, processed_at, status, resolved_at, id)',
  // OceanBase/ZDAS requires ALTER-based indexes; its dialect skips standalone CREATE INDEX.
  mysql: 'ALTER TABLE approval_cards ADD INDEX idx_approval_cards_polling (delivery_mode, processed_at, status, resolved_at, id)',
};

export const approvalConsumptionMigrations: readonly Migration[] = [{
  version: 159,
  description: 'Track approval-card workflow consumption without rewriting decisions',
  sql: [],
  async migrate(db) {
    // Keep column-before-index ordering and tolerate only this migration's already-applied DDL.
    // Other failures must propagate before runMigrations writes the version ledger.
    try {
      await db.exec(db.dialect.renderDdl(approvalConsumptionColumnDdl));
    } catch (error) {
      if (!/duplicate column name\b/i.test(String(error))) throw error;
    }
    const indexDdl = db.dbType === 'sqlite'
      ? approvalConsumptionIndexDdl.sqlite
      : approvalConsumptionIndexDdl.mysql;
    try {
      await db.exec(db.dialect.renderDdl(indexDdl));
    } catch (error) {
      if (!/duplicate key name\b/i.test(String(error))) throw error;
    }
  },
}];
