import type { IDatabase } from './db.js';
import type { migrations } from './schema.js';

/** Only privilege denials from these specific optional DDL statements defer progress. */
export class OrdinarySchemaDdlDenied extends Error {
  constructor(readonly version: 150 | 151, readonly operation: 'CREATE' | 'ALTER', cause: unknown) {
    super(`Ordinary request ${operation} denied`, { cause });
  }
}

export class OrdinaryRequestSchemaIncompatible extends Error {
  readonly version = 150;
  constructor(detail: string) {
    super(`Incompatible ordinary request schema: ${detail}`);
  }
}

export class OrdinaryMarkerSchemaIncompatible extends Error {
  readonly version = 151;
  constructor(table: string, column: string) {
    super(`Incompatible ordinary marker column: ${table}.${column}`);
  }
}

/** SQLite applies INT affinity before CHAR/CLOB/TEXT, even for names like CHARINT. */
export function isSqliteOrdinaryText(type: string): boolean {
  const upper = (type || '').toUpperCase();
  return !upper.includes('INT') && ['CHAR', 'CLOB', 'TEXT'].some(part => upper.includes(part));
}

/** Markers must also be nullable, non-primary-key columns without a value default. */
export function isCompatibleSqliteOrdinaryMarker(column: {
  type: string; notnull: number; pk: number; dflt_value: string | null;
}): boolean {
  return column.pk === 0 && column.notnull === 0
    && (column.dflt_value == null || column.dflt_value.toUpperCase() === 'NULL')
    && isSqliteOrdinaryText(column.type);
}

const ordinaryInsertColumns = new Set(['request_key_hash', 'parent_flow_id', 'child_flow_id',
  'child_workflow_id', 'request_fingerprint', 'parent_run_marker', 'child_run_marker']);

/** Reject unprovided mandatory columns, but leave v151 marker validation to v151. */
export function hasRequiredOrdinaryExtra(columns: Array<{ name: string; notnull: number; dflt_value: string | null; hidden?: number }>): boolean {
  return columns.some(c => !ordinaryInsertColumns.has(c.name) && Number(c.hidden ?? 0) === 0
    && Number(c.notnull) === 1 && (c.dflt_value == null || /^\(*\s*NULL\s*\)*$/i.test(c.dflt_value.trim())));
}

export function isMysqlGeneratedColumn(column: { extra?: string; generation_expression?: string | null }): boolean {
  return /\b(?:VIRTUAL|STORED)\s+GENERATED\b/i.test(column.extra ?? '') || !!column.generation_expression?.trim();
}

export function hasRequiredMysqlOrdinaryExtra(columns: Array<{ column_name: string; is_nullable: string; column_default?: string | null; extra?: string; generation_expression?: string | null }>): boolean {
  return columns.some(c => !ordinaryInsertColumns.has(c.column_name) && c.is_nullable === 'NO'
    && c.column_default == null && !/\bauto_increment\b/i.test(c.extra ?? '') && !isMysqlGeneratedColumn(c));
}

function errorCode(error: unknown): { code?: unknown; errno?: unknown } {
  return typeof error === 'object' && error !== null ? error : {};
}

function duplicateColumn(error: unknown, dbType: IDatabase['dbType']): boolean {
  const { code, errno } = errorCode(error);
  const message = error instanceof Error ? error.message : '';
  return dbType === 'sqlite'
    ? code === 'SQLITE_ERROR' && /^duplicate column name:/i.test(message)
    : dbType === 'mysql' && (code === 'ER_DUP_FIELDNAME' || errno === 1060);
}

function ddlDenied(error: unknown, dbType: IDatabase['dbType'], operation: 'CREATE' | 'ALTER'): boolean {
  const { code, errno } = errorCode(error);
  if (dbType === 'sqlite') return code === 'SQLITE_AUTH';
  const message = error instanceof Error ? error.message : '';
  return dbType === 'mysql' && (code === 'ER_TABLEACCESS_DENIED_ERROR' || errno === 1142)
    && new RegExp(`^${operation} command denied\\b`, 'i').test(message);
}

/** Request keys and input fingerprints are hashes; neither params nor credentials are stored here. */
export const ordinaryRunRequestDdl = `CREATE TABLE IF NOT EXISTS ordinary_run_requests (
  request_key_hash VARCHAR(64) NOT NULL PRIMARY KEY,
  parent_flow_id VARCHAR(128) NOT NULL,
  child_flow_id VARCHAR(128) NOT NULL,
  child_workflow_id VARCHAR(128) NOT NULL,
  request_fingerprint VARCHAR(64) NOT NULL,
  parent_run_marker VARCHAR(64),
  child_run_marker VARCHAR(64),
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE INDEX uk_ordinary_run_child (child_flow_id)
)`;

/** Validate v150 independently of the markers introduced by v151. Metadata errors
 * propagate unchanged; only confirmed incompatible definitions defer migration. */
async function assertV150Schema(db: IDatabase): Promise<void> {
  const required: Array<[string, number]> = [
    ['request_key_hash', 64], ['parent_flow_id', 128], ['child_flow_id', 128],
    ['child_workflow_id', 128], ['request_fingerprint', 64],
  ];
  if (db.dbType === 'sqlite') {
    const columns = await db.query<{ name: string; type: string; notnull: number; pk: number; dflt_value: string | null; hidden: number }>(
      'PRAGMA table_xinfo(ordinary_run_requests)');
    if (!required.every(([name]) => columns.some(c => c.name === name && Number(c.hidden) === 0 && Number(c.notnull) === 1 && isSqliteOrdinaryText(c.type)))
      || !columns.some(c => c.name === 'request_key_hash' && Number(c.pk) === 1)
      || columns.filter(c => Number(c.pk) > 0).length !== 1 || hasRequiredOrdinaryExtra(columns))
      throw new OrdinaryRequestSchemaIncompatible('required columns or primary key');
    const indexes = await db.query<{ name: string; unique: number; partial: number }>('PRAGMA index_list(ordinary_run_requests)');
    for (const index of indexes.filter(i => Number(i.unique) === 1 && Number(i.partial) === 0)) {
      const parts = await db.query<{ name: string }>('SELECT name FROM pragma_index_info(?)', [index.name]);
      if (parts.length === 1 && parts[0].name === 'child_flow_id') return;
    }
    throw new OrdinaryRequestSchemaIncompatible('child_flow_id unique index');
  }
  const tables = await db.query<{ engine: string }>(`SELECT ENGINE AS engine FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ordinary_run_requests'`);
  if (tables.length !== 1 || tables[0].engine?.toLowerCase() !== 'innodb')
    throw new OrdinaryRequestSchemaIncompatible('transactional table');
  const columns = await db.query<{ column_name: string; data_type: string; max_length: number | string | null; is_nullable: string; column_default: string | null; extra: string; generation_expression: string | null }>(
    `SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length,
      IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, EXTRA AS extra, GENERATION_EXPRESSION AS generation_expression FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ordinary_run_requests'`);
  if (!required.every(([name, length]) => columns.some(c => c.column_name === name
    && ['char', 'varchar'].includes(c.data_type?.toLowerCase()) && c.max_length != null
    && Number.isFinite(Number(c.max_length)) && Number(c.max_length) >= length && c.is_nullable === 'NO' && !isMysqlGeneratedColumn(c))))
    throw new OrdinaryRequestSchemaIncompatible('required columns');
  if (hasRequiredMysqlOrdinaryExtra(columns)) throw new OrdinaryRequestSchemaIncompatible('extra required column');
  const indexes = await db.query<{ index_name: string; column_name: string; non_unique: number; sub_part: number | null; seq: number }>(
    `SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name, NON_UNIQUE AS non_unique,
      SUB_PART AS sub_part, SEQ_IN_INDEX AS seq FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ordinary_run_requests'`);
  const fullUnique = (column: string) => indexes.some(i => i.column_name === column && Number(i.non_unique) === 0
    && i.sub_part == null && Number(i.seq) === 1 && indexes.filter(j => j.index_name === i.index_name).length === 1);
  if (!fullUnique('request_key_hash') || !fullUnique('child_flow_id'))
    throw new OrdinaryRequestSchemaIncompatible('unique indexes');
}

async function sqliteMarkerExists(db: IDatabase, table: string, column: string): Promise<boolean> {
  const columns = await db.query<{ name: string; type: string; notnull: number; pk: number; dflt_value: string | null; hidden: number }>(`PRAGMA table_xinfo(${table})`);
  const found = columns.find(c => c.name.toLowerCase() === column);
  if (!found) return false;
  if (found.hidden !== 0 || !isCompatibleSqliteOrdinaryMarker(found))
    throw new OrdinaryMarkerSchemaIncompatible(table, column);
  return true;
}

/** Confirm only a peer's matching ledger row and version-specific schema; this
 * does not assert the ordinary capability is ready to serve requests. */
export async function confirmConcurrentOrdinarySqlite(db: IDatabase, version: number): Promise<boolean> {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === version);
  if (db.dbType !== 'sqlite' || !migration) return false;
  const rows = await db.query<{ description: string }>('SELECT description FROM schema_version WHERE version = ?', [version]);
  if (!rows.length || rows.some(row => row.description !== migration.description)) return false;
  await assertV150Schema(db);
  if (version === 151) {
    for (const [table, column] of [['flow_runs', 'ordinary_run_marker'],
      ['ordinary_run_requests', 'parent_run_marker'], ['ordinary_run_requests', 'child_run_marker']]) {
      if (!await sqliteMarkerExists(db, table, column)) throw new OrdinaryMarkerSchemaIncompatible(table, column);
    }
  }
  return true;
}

export const ordinaryRunRequestMigrations: Array<(typeof migrations)[number] & { supportedDbTypes: readonly ("sqlite" | "mysql")[] }> = [{
  version: 150,
  description: 'Store idempotent ordinary run request associations',
  sql: [],
  supportedDbTypes: ['sqlite', 'mysql'],
  async migrate(db: IDatabase) {
    if (db.dbType !== 'sqlite' && db.dbType !== 'mysql') throw new Error('Unsupported database');
    const ddl = db.dialect.renderDdl(ordinaryRunRequestDdl);
    try {
      await db.exec(db.dbType === 'mysql' ? `${ddl} ENGINE=InnoDB` : ddl);
    } catch (error) {
      if (ddlDenied(error, db.dbType, 'CREATE')) throw new OrdinarySchemaDdlDenied(150, 'CREATE', error);
      throw error;
    }
    await assertV150Schema(db);
  },
}, {
  version: 151,
  description: 'Bind ordinary request associations to run incarnations',
  sql: [],
  supportedDbTypes: ['sqlite', 'mysql'],
  async migrate(db: IDatabase) {
    // MySQL DDL commits implicitly. Inspect each column so interrupted upgrades
    // can safely resume without changing or deleting any pre-existing rows.
    for (const [table, column] of [
      ['flow_runs', 'ordinary_run_marker'],
      ['ordinary_run_requests', 'parent_run_marker'],
      ['ordinary_run_requests', 'child_run_marker'],
    ]) {
      const hasCompatibleColumn = async () => {
        if (db.dbType === 'sqlite') {
          return sqliteMarkerExists(db, table, column);
        }
        const columns = await db.query<{ name: string; data_type: string; max_length: number | string | null; is_nullable: string; column_default: string | null; extra: string; generation_expression: string | null }>(
          `SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length,
            IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, EXTRA AS extra, GENERATION_EXPRESSION AS generation_expression FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?`, [table]);
        const found = columns.find(c => c.name.toLowerCase() === column);
        if (!found) return false;
        if (!['char', 'varchar'].includes(found.data_type?.toLowerCase()) || Number(found.max_length) < 64
          || !Number.isFinite(Number(found.max_length)) || found.is_nullable !== 'YES' || found.column_default != null || isMysqlGeneratedColumn(found))
          throw new OrdinaryMarkerSchemaIncompatible(table, column);
        return true;
      };
      if (await hasCompatibleColumn()) continue;
      try {
        await db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} VARCHAR(64) DEFAULT NULL`);
      } catch (error) {
        if (duplicateColumn(error, db.dbType)) {
          if (await hasCompatibleColumn()) continue;
          throw error;
        }
        if (ddlDenied(error, db.dbType, 'ALTER')) throw new OrdinarySchemaDdlDenied(151, 'ALTER', error);
        throw error;
      }
    }
  },
}];
