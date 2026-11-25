import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { isCompatibleSqliteOrdinaryMarker, isSqliteOrdinaryText, hasRequiredOrdinaryExtra, hasRequiredMysqlOrdinaryExtra, isMysqlGeneratedColumn } from '@avernet/clawweb-shared/server/schema-ordinary-run-request';

export type OrdinaryRequestRow = { request_key_hash: string; parent_flow_id: string; child_flow_id: string;
  child_workflow_id: string; request_fingerprint: string; parent_run_marker: string | null; child_run_marker: string | null };

/** Check externally provisioned schemas too: silent truncation or non-transactional
 * tables invalidate both uniqueness and rollback. Unsupported databases fail closed. */
export async function assertOrdinaryRequestSchema(db: IDatabase): Promise<void> {
  if (db.dbType === 'sqlite') {
    const columns = await db.query<{ name: string; type: string; notnull: number; pk: number; dflt_value: string | null; hidden: number }>('PRAGMA table_xinfo(ordinary_run_requests)');
    if (!['request_key_hash', 'parent_flow_id', 'child_flow_id', 'child_workflow_id', 'request_fingerprint']
      .every(name => columns.some(c => c.name === name && c.hidden === 0 && c.notnull === 1 && isSqliteOrdinaryText(c.type)))
      || !columns.some(c => c.name === 'request_key_hash' && c.pk === 1)
      || columns.filter(c => c.pk > 0).length !== 1 || hasRequiredOrdinaryExtra(columns)) throw new Error('Invalid ordinary request schema');
    const runColumns = await db.query<{ name: string; type: string; notnull: number; pk: number; dflt_value: string | null; hidden: number }>('PRAGMA table_xinfo(flow_runs)');
    if (!['parent_run_marker', 'child_run_marker'].every(name => columns.some(c => c.name === name && c.hidden === 0 && isCompatibleSqliteOrdinaryMarker(c)))
      || !runColumns.some(c => c.name === 'ordinary_run_marker' && c.hidden === 0 && isCompatibleSqliteOrdinaryMarker(c))) throw new Error('Invalid ordinary request marker columns');
    const fullUnique = async (table: string, column: string) => {
      const indexes = await db.query<{ name: string; unique: number; partial: number }>(`PRAGMA index_list(${table})`);
      for (const index of indexes.filter(i => Number(i.unique) === 1 && Number(i.partial) === 0)) {
        const parts = await db.query<{ name: string }>('SELECT name FROM pragma_index_info(?)', [index.name]);
        if (parts.length === 1 && parts[0].name === column) return true;
      }
      return false;
    };
    if (!await fullUnique('flow_runs', 'flow_id') || !await fullUnique('ordinary_run_requests', 'child_flow_id'))
      throw new Error('Invalid ordinary request unique indexes');
    return;
  }
  if (db.dbType !== 'mysql') throw new Error('Unsupported ordinary request database');
  const modes = await db.query<{ sql_mode: string }>('SELECT @@SESSION.sql_mode AS sql_mode');
  if (!modes[0]?.sql_mode?.split(',').some(mode => ['STRICT_TRANS_TABLES', 'STRICT_ALL_TABLES'].includes(mode.trim().toUpperCase())))
    throw new Error('Ordinary request writes require strict MySQL mode');
  const tables = await db.query<{ table_name: string; engine: string }>(`SELECT TABLE_NAME AS table_name, ENGINE AS engine
    FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('ordinary_run_requests','flow_runs')`);
  if (!['ordinary_run_requests', 'flow_runs'].every(name => tables.some(t => t.table_name === name && t.engine?.toLowerCase() === 'innodb')))
    throw new Error('Ordinary request tables require transactional storage');
  const columns = await db.query<{ table_name: string; column_name: string; data_type: string; max_length: number; is_nullable: string; column_default: string | null; extra: string; generation_expression: string | null }>(
    `SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length, IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, EXTRA AS extra, GENERATION_EXPRESSION AS generation_expression
     FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('ordinary_run_requests','flow_runs')`);
  const required: Array<[string, string, number]> = [
    ['ordinary_run_requests','request_key_hash',64], ['ordinary_run_requests','parent_flow_id',128],
    ['ordinary_run_requests','child_flow_id',128], ['ordinary_run_requests','child_workflow_id',128],
    ['ordinary_run_requests','request_fingerprint',64], ['flow_runs','flow_id',128], ['flow_runs','workflow_id',128],
  ];
  const markers = [['ordinary_run_requests', 'parent_run_marker'], ['ordinary_run_requests', 'child_run_marker'],
    ['flow_runs', 'ordinary_run_marker']];
  if (!markers.every(([table, name]) => columns.some(c => c.table_name === table && c.column_name === name
    && ['char', 'varchar'].includes(c.data_type?.toLowerCase()) && Number(c.max_length) >= 64 && c.is_nullable === 'YES' && c.column_default == null && !isMysqlGeneratedColumn(c))))
    throw new Error('Invalid ordinary request marker columns');
  if (!required.every(([table, name, length]) => columns.some(c => c.table_name === table && c.column_name === name
    && ['char', 'varchar'].includes(c.data_type?.toLowerCase()) && Number(c.max_length) >= length && c.is_nullable === 'NO' && !isMysqlGeneratedColumn(c)))) throw new Error('Invalid ordinary request columns');
  if (hasRequiredMysqlOrdinaryExtra(columns.filter(c => c.table_name === 'ordinary_run_requests'))) throw new Error('Invalid ordinary request columns');
  const indexes = await db.query<{ table_name: string; index_name: string; column_name: string; non_unique: number; sub_part: number | null; seq: number }>(
    `SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name, COLUMN_NAME AS column_name, NON_UNIQUE AS non_unique,
      SUB_PART AS sub_part, SEQ_IN_INDEX AS seq FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('ordinary_run_requests','flow_runs')`);
  const fullUnique = (table: string, column: string) => indexes.some(i => {
    const group = indexes.filter(j => j.table_name === table && j.index_name === i.index_name);
    return i.table_name === table && i.column_name === column && Number(i.non_unique) === 0
      && i.sub_part == null && Number(i.seq) === 1 && group.length === 1;
  });
  if (!fullUnique('ordinary_run_requests','request_key_hash') || !fullUnique('ordinary_run_requests','child_flow_id')
    || !fullUnique('flow_runs','flow_id'))
    throw new Error('Invalid ordinary request unique indexes');
}

export class OrdinaryRunRequestRepository {
  constructor(private readonly db: IDatabase) {}
  async find(keyHash: string): Promise<OrdinaryRequestRow | null> {
    const sql = `SELECT request_key_hash,parent_flow_id,child_flow_id,child_workflow_id,request_fingerprint,parent_run_marker,child_run_marker
       FROM ordinary_run_requests WHERE request_key_hash = ?`;
    const rows = await this.db.query<OrdinaryRequestRow>(sql, [keyHash]);
    if (!rows[0] || this.db.dbType !== 'mysql') return rows[0] ?? null;
    // Lock and re-read existing associations, but avoid a FOR UPDATE gap lock
    // on absent keys: competing first registrations could otherwise deadlock.
    const locked = await this.db.query<OrdinaryRequestRow>(`${sql} FOR UPDATE`, [keyHash]);
    return locked[0] ?? null;
  }
  async insert(row: OrdinaryRequestRow): Promise<void> {
    const result = await this.db.exec(`INSERT INTO ordinary_run_requests
      (request_key_hash,parent_flow_id,child_flow_id,child_workflow_id,request_fingerprint,parent_run_marker,child_run_marker) VALUES (?,?,?,?,?,?,?)`,
    [row.request_key_hash,row.parent_flow_id,row.child_flow_id,row.child_workflow_id,row.request_fingerprint,row.parent_run_marker,row.child_run_marker]);
    if (result.affectedRows !== 1) throw new Error('Ordinary request insertion failed');
  }
}
