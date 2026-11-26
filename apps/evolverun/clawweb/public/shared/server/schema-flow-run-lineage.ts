import type { IDatabase } from './db.js';
import type { Migration } from './schema-types.js';

const lineageColumns = ['parent_flow_id', 'root_flow_id'] as const;

/** Additive only: old writers and existing rows keep NULL, with no JSON backfill. */
export const flowRunLineageDdl = [
  "ALTER TABLE flow_runs ADD COLUMN parent_flow_id VARCHAR(255) NULL DEFAULT NULL COMMENT 'Direct parent workflow run ID'",
  "ALTER TABLE flow_runs ADD COLUMN root_flow_id VARCHAR(255) NULL DEFAULT NULL COMMENT 'Root ancestor workflow run ID'",
];

function incompatible(column: string): Error {
  return new Error(`flow_runs.${column} is not compatibly provisioned; review scripts/sql/flow-run-lineage-v157.mysql.sql`);
}

/** Inspect only lineage columns; never repair or overwrite an existing definition. */
async function compatibleColumns(db: IDatabase): Promise<Set<string>> {
  const present = new Set<string>();
  if (db.dbType === 'sqlite') {
    const columns = await db.query<{
      name: string; type: string; notnull: number; pk: number; dflt_value: string | null; hidden: number;
    }>('PRAGMA table_xinfo(flow_runs)');
    for (const name of lineageColumns) {
      const column = columns.find(c => c.name.toLowerCase() === name);
      if (!column) continue;
      // SQLite applies INT affinity before CHAR/CLOB/TEXT (including CHARINT).
      const type = column.type.toUpperCase();
      if (column.hidden !== 0 || column.pk !== 0 || column.notnull !== 0 || type.includes('INT')
        || !['CHAR', 'CLOB', 'TEXT'].some(part => type.includes(part))
        || (column.dflt_value != null && !/^\(*\s*NULL\s*\)*$/i.test(column.dflt_value))) throw incompatible(name);
      present.add(name);
    }
    return present;
  }
  const columns = await db.query<{
    column_name: string; data_type: string; max_length: number | string | null;
    is_nullable: string; column_default: string | null; extra: string; generation_expression: string | null;
  }>(`SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length,
    IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, EXTRA AS extra,
    GENERATION_EXPRESSION AS generation_expression FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_runs'
      AND COLUMN_NAME IN ('parent_flow_id', 'root_flow_id')`);
  for (const name of lineageColumns) {
    const column = columns.find(c => c.column_name.toLowerCase() === name);
    if (!column) continue;
    if (column.data_type.toLowerCase() !== 'varchar' || !Number.isFinite(Number(column.max_length))
      || Number(column.max_length) < 255 || column.is_nullable !== 'YES' || column.column_default != null
      || /\b(?:VIRTUAL|STORED)\s+GENERATED\b/i.test(column.extra ?? '') || column.generation_expression?.trim())
      throw incompatible(name);
    present.add(name);
  }
  return present;
}

/** Managed/external startup is read-only: no DDL, ledger access, or ordinary-request readiness probes. */
export async function assertFlowRunLineageSchema(db: IDatabase): Promise<void> {
  const present = await compatibleColumns(db);
  for (const name of lineageColumns) if (!present.has(name)) throw incompatible(name);
}

function duplicateColumn(error: unknown, dbType: IDatabase['dbType']): boolean {
  const { code, errno } = (typeof error === 'object' && error !== null ? error : {}) as { code?: unknown; errno?: unknown };
  if ((dbType === 'mysql' || dbType === 'zdas') && (code === 'ER_DUP_FIELDNAME' || errno === 1060)) return true;
  return error instanceof Error && (dbType === 'sqlite'
    ? /^duplicate column name\s*:/i.test(error.message)
    : /^duplicate column name\b/i.test(error.message));
}

export const flowRunLineageMigrations: readonly Migration[] = [{
  // v155/v156 are retired no-DDL tombstones in the assembled registry, not free IDs.
  version: 157,
  description: 'Add nullable parent and root lineage to flow runs',
  sql: [],
  async migrate(db) {
    const present = await compatibleColumns(db);
    for (const [index, name] of lineageColumns.entries()) {
      if (present.has(name)) continue;
      const ddl = flowRunLineageDdl[index];
      // Like v152 markers, these unindexed identifiers must bypass MySQL's 190-char narrowing.
      try { await db.exec(db.dbType === 'sqlite' ? db.dialect.renderDdl(ddl) : ddl); }
      catch (error) {
        // Only a concurrent duplicate with a verified compatible definition is a no-op.
        if (!duplicateColumn(error, db.dbType) || !(await compatibleColumns(db)).has(name)) throw error;
      }
    }
    await assertFlowRunLineageSchema(db);
  },
}];
