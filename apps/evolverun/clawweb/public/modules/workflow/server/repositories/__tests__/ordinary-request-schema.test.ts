import { describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { mysqlDialect, sqliteDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { assertOrdinaryRequestSchema, OrdinaryRunRequestRepository } from '../ordinary-run-request-repository.js';

function fixture() {
  const tables = ['ordinary_run_requests','flow_runs'].map(table_name => ({ table_name, engine: 'InnoDB' }));
  const columns = [
    ['ordinary_run_requests','request_key_hash',64], ['ordinary_run_requests','parent_flow_id',128],
    ['ordinary_run_requests','child_flow_id',128], ['ordinary_run_requests','child_workflow_id',128],
    ['ordinary_run_requests','request_fingerprint',64], ['ordinary_run_requests','parent_run_marker',64],
    ['ordinary_run_requests','child_run_marker',64], ['flow_runs','ordinary_run_marker',64], ['flow_runs','flow_id',190], ['flow_runs','workflow_id',190],
  ].map(([table_name, column_name, max_length]) => ({ table_name, column_name, max_length, data_type: 'varchar', is_nullable: String(column_name).endsWith('marker') ? 'YES' : 'NO', column_default: null as string | null }));
  const indexes = [
    ['ordinary_run_requests','PRIMARY','request_key_hash'], ['ordinary_run_requests','uk_child','child_flow_id'],
    ['flow_runs','uk_flow','flow_id'],
  ].map(([table_name,index_name,column_name]) => ({ table_name,index_name,column_name,non_unique:0,sub_part:null as number | null,seq:1 }));
  const session = [{ sql_mode: 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION' }];
  const query = vi.fn(async (sql: string) => sql.includes('@@SESSION.sql_mode') ? session : sql.includes('information_schema.TABLES') ? tables :
    sql.includes('information_schema.COLUMNS') ? columns : indexes);
  return { session, db: { dbType: 'mysql', dialect: mysqlDialect, query } as unknown as IDatabase, tables, columns, indexes, query };
}
describe('ordinary request schema fail-closed checks', () => {
  it('accepts full transactional MySQL metadata, but rejects non-InnoDB and narrowed columns', async () => {
    const f = fixture(); await expect(assertOrdinaryRequestSchema(f.db)).resolves.toBeUndefined();
    f.tables[1].engine = 'MyISAM'; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    f.tables[1].engine = 'InnoDB'; f.columns[1].max_length = 64; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
  });
  it('rejects prefix, non-unique, or composite keys and propagates metadata failures', async () => {
    const f = fixture(); f.indexes[0].sub_part = 32; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    f.indexes[0].sub_part = null; f.indexes[1].non_unique = 1; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    f.indexes[1].non_unique = 0; f.indexes.push({ ...f.indexes[0], column_name: 'other', seq: 2 });
    await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    f.query.mockRejectedValueOnce(new Error('metadata failed')); await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow('metadata failed');
  });
  it('rejects missing, narrow, non-null or defaulted incarnation markers', async () => {
    const f = fixture(); const marker = f.columns.find(c => c.column_name === 'ordinary_run_marker')!;
    marker.max_length = 32; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    marker.max_length = 64; marker.is_nullable = 'NO'; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    marker.is_nullable = 'YES'; marker.column_default = 'reused'; await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
    f.columns.splice(f.columns.indexOf(marker), 1); await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
  });
  it('rejects non-strict MySQL sessions that may silently truncate run inputs', async () => {
    const f = fixture(); f.session[0].sql_mode = 'NO_ENGINE_SUBSTITUTION';
    await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow();
  });
  it.each(['zdas','noop'] as const)('does not claim transaction support for %s', async dbType => {
    await expect(assertOrdinaryRequestSchema({ dbType, dialect: sqliteDialect } as IDatabase)).rejects.toThrow('Unsupported');
  });
});

it('rejects a legacy SQLite schema and a marker with a reusable default', async () => {
  const { default: Database } = await import('better-sqlite3');
  const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
  const { ordinaryRunRequestDdl } = await import('@avernet/clawweb-shared/server/schema-ordinary-run-request');
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  try {
    raw.exec('CREATE TABLE flow_runs (flow_id TEXT UNIQUE)');
    await db.exec(db.dialect.renderDdl(ordinaryRunRequestDdl));
    await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('marker');
    raw.exec("ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker TEXT DEFAULT 'reused'");
    await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('marker');
  } finally { await db.close(); }
});


it('locks and re-reads existing MySQL associations without gap-locking absent keys', async () => {
  const old = { request_key_hash: 'key', child_flow_id: 'old' };
  const current = { request_key_hash: 'key', child_flow_id: 'current' };
  const query = vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce([old]).mockResolvedValueOnce([current]);
  const repo = new OrdinaryRunRequestRepository({ dbType: 'mysql', query } as unknown as IDatabase);
  expect(await repo.find('missing')).toBeNull();
  expect(query).toHaveBeenCalledTimes(1);
  expect(await repo.find('key')).toEqual(current);
  expect(query.mock.calls[2][0]).toMatch(/FOR UPDATE$/);
});

it.each(['ordinary_run_marker', 'parent_run_marker', 'child_run_marker'].flatMap(name =>
  ['INTEGER', 'NUMERIC', 'REAL', 'BLOB', 'CHARINT'].map(definition => ({ name, definition }))))
  ('rejects a non-text SQLite marker $name with $definition affinity', async ({ name, definition }) => {
    const { default: Database } = await import('better-sqlite3');
    const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    const marker = (key: string) => `${key} ${key === name ? definition : 'TEXT'}`;
    raw.exec(`CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ${marker('ordinary_run_marker')});
      CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
        child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
        ${marker('parent_run_marker')}, ${marker('child_run_marker')});`);
    try { await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('Invalid ordinary request marker columns'); }
    finally { await db.close(); }
  });

it('rejects an SQLite INTEGER PRIMARY KEY run marker despite nullable metadata', async () => {
  const { default: Database } = await import('better-sqlite3');
  const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
  const { ordinaryRunRequestDdl } = await import('@avernet/clawweb-shared/server/schema-ordinary-run-request');
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec('CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ordinary_run_marker INTEGER PRIMARY KEY)');
  await db.exec(db.dialect.renderDdl(ordinaryRunRequestDdl));
  try { await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('Invalid ordinary request marker columns'); }
  finally { await db.close(); }
});

it.each(['TEXT', 'VARCHAR(64)', 'CLOB'])('accepts SQLite %s marker columns and preserves numeric and hexadecimal strings', async definition => {
  const { default: Database } = await import('better-sqlite3');
  const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ordinary_run_marker ${definition});
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
      parent_run_marker ${definition}, child_run_marker ${definition});`);
  try {
    await expect(assertOrdinaryRequestSchema(db)).resolves.toBeUndefined();
    const markers = ['1'.repeat(64), 'a1'.repeat(32)];
    for (const [i, value] of markers.entries()) {
      raw.prepare('INSERT INTO flow_runs VALUES (?, ?)').run(`flow${i}`, value);
      raw.prepare('INSERT INTO ordinary_run_requests VALUES (?, ?, ?, ?, ?, ?, ?)').run(`key${i}`, 'parent', `child${i}`, 'wf', 'fingerprint', value, value);
    }
    expect(raw.prepare('SELECT ordinary_run_marker AS value, typeof(ordinary_run_marker) AS type FROM flow_runs ORDER BY flow_id').all())
      .toEqual(markers.map(value => ({ value, type: 'text' })));
    expect(raw.prepare('SELECT parent_run_marker AS parent, child_run_marker AS child FROM ordinary_run_requests ORDER BY request_key_hash').all())
      .toEqual(markers.map(value => ({ parent: value, child: value })));
  } finally { await db.close(); }
});

it('rejects SQLite request identifiers with numeric affinity', async () => {
  const { default: Database } = await import('better-sqlite3');
  const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ordinary_run_marker TEXT);
    CREATE TABLE ordinary_run_requests (request_key_hash NUMERIC NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
      parent_run_marker TEXT, child_run_marker TEXT);`);
  try { await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('Invalid ordinary request schema'); }
  finally { await db.close(); }
});

it('accepts a SQLite unique index with an embedded quote at runtime', async () => {
  const { default: Database } = await import('better-sqlite3');
  const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ordinary_run_marker TEXT);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
      parent_run_marker TEXT, child_run_marker TEXT); CREATE UNIQUE INDEX "child""index" ON ordinary_run_requests(child_flow_id);`);
  try { await expect(assertOrdinaryRequestSchema(db)).resolves.toBeUndefined(); } finally { await db.close(); }
});

it.each(['VIRTUAL GENERATED', 'STORED GENERATED'])('rejects MySQL %s marker at runtime', async extra => {
  const f = fixture();
  Object.assign(f.columns.find(c => c.column_name === 'ordinary_run_marker')!, { extra, generation_expression: "'constant'" });
  await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow('Invalid ordinary request marker');
});

it('rejects a generated required MySQL association column at runtime', async () => {
  const f = fixture();
  Object.assign(f.columns.find(c => c.column_name === 'request_fingerprint')!, { extra: 'VIRTUAL GENERATED', generation_expression: "'fixed'" });
  await expect(assertOrdinaryRequestSchema(f.db)).rejects.toThrow('Invalid ordinary request columns');
});

it('rejects an unprovided required SQLite column at runtime', async () => {
  const { default: Database } = await import('better-sqlite3');
  const { SqliteDatabase } = await import('@avernet/clawweb-shared/server/db/sqlite-database');
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ordinary_run_marker TEXT);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
      parent_run_marker TEXT, child_run_marker TEXT, extra TEXT NOT NULL);`);
  try { await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('Invalid ordinary request schema'); }
  finally { await db.close(); }
});
