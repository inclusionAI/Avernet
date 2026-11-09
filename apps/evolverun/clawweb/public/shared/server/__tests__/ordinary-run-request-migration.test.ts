import { expect, it, vi } from 'vitest';
import { ordinaryRunRequestMigrations } from '../schema-ordinary-run-request.js';
import type { IDatabase } from '../db.js';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { sqliteDialect } from '../db/dialect.js';
import { runMigrations } from '../db.js';
import { sqliteTriggers } from '../schema.js';

function seedBaseTriggers(db: Database.Database) {
  for (const sql of sqliteTriggers.filter(ddl => /trg_(flow_runs|schema_version)_update\b/.test(ddl))) db.exec(sql);
}

it('provisions v150 once on an existing SQLite schema', async () => {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  raw.exec("CREATE TABLE flow_runs (flow_id TEXT); CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'previous')");
  await runMigrations(db, 'sqlite');
  await runMigrations(db, 'sqlite');
  expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=150').get()).toEqual({ n: 1 });
  expect(raw.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='ordinary_run_requests'").get()).toBeTruthy();
  await db.close();
});


it('upgrades old associations without modifying rows and resumes after a partially applied SQLite upgrade', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT, ordinary_run_marker VARCHAR(64));
    INSERT INTO flow_runs (flow_id) VALUES ('parent'), ('child');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY, parent_flow_id TEXT, child_flow_id TEXT,
      child_workflow_id TEXT, request_fingerprint TEXT, parent_run_marker VARCHAR(64));
    INSERT INTO ordinary_run_requests VALUES ('key','parent','child','wf','fingerprint',NULL);`);
  try {
    await runMigrations(db, 'sqlite'); await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT * FROM ordinary_run_requests').get()).toEqual({ request_key_hash: 'key', parent_flow_id: 'parent',
      child_flow_id: 'child', child_workflow_id: 'wf', request_fingerprint: 'fingerprint', parent_run_marker: null, child_run_marker: null });
    expect(raw.prepare('SELECT count(*) AS n FROM flow_runs').get()).toEqual({ n: 2 });
    expect(raw.prepare('SELECT count(*) AS n FROM schema_version WHERE version=151').get()).toEqual({ n: 1 });
  } finally { await db.close(); }
});

it('resumes MySQL marker DDL without destructive statements and preserves existing columns', async () => {
  const existing: Record<string, Set<string>> = { flow_runs: new Set(['ordinary_run_marker']), ordinary_run_requests: new Set(['parent_run_marker']) };
  const query = vi.fn(async (_sql: string, params: unknown[]) => [...existing[String(params[0])]].map(name => ({ name, data_type: 'varchar', max_length: 64, is_nullable: 'YES', column_default: null })));
  const exec = vi.fn(async (sql: string) => {
    const match = /^ALTER TABLE (\w+) ADD COLUMN (\w+) VARCHAR\(64\) DEFAULT NULL$/.exec(sql);
    if (!match) throw new Error('Unexpected DDL');
    existing[match[1]].add(match[2]);
    return { affectedRows: 0 };
  });
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  await migration.migrate!({ dbType: 'mysql', query, exec } as unknown as IDatabase);
  await migration.migrate!({ dbType: 'mysql', query, exec } as unknown as IDatabase);
  expect(exec).toHaveBeenCalledTimes(1);
  expect(exec).toHaveBeenCalledWith('ALTER TABLE ordinary_run_requests ADD COLUMN child_run_marker VARCHAR(64) DEFAULT NULL');
});

it('concurrently migrates two SQLite connections after both inspect the old schema', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-migration-'));
  const raw1 = new Database(join(dir, 'db.sqlite'));
  const raw2 = new Database(join(dir, 'db.sqlite'));
  raw1.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY);`);
  let arrivals = 0;
  let release!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  function connection(raw: Database.Database): IDatabase {
    const db = new SqliteDatabase(raw);
    return {
      dbType: db.dbType, dialect: db.dialect,
      exec: db.exec.bind(db), transaction: db.transaction.bind(db), close: db.close.bind(db),
      async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
        const rows = await db.query<T>(sql, params);
        if (sql === 'PRAGMA table_xinfo(flow_runs)') {
          if (++arrivals === 2) release();
          await barrier;
        }
        return rows;
      },
    };
  }
  const db1 = connection(raw1), db2 = connection(raw2);
  try {
    await Promise.all([runMigrations(db1, 'sqlite'), runMigrations(db2, 'sqlite')]);
    expect(arrivals).toBeGreaterThanOrEqual(2);
    expect(raw1.prepare('PRAGMA table_info(flow_runs)').all()).toEqual(expect.arrayContaining([expect.objectContaining({ name: 'ordinary_run_marker' })]));
    expect(raw1.prepare('PRAGMA table_info(ordinary_run_requests)').all()).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: 'parent_run_marker' }), expect.objectContaining({ name: 'child_run_marker' }),
    ]));
  } finally { await db1.close(); await db2.close(); rmSync(dir, { recursive: true, force: true }); }
});

it('propagates duplicate-column errors if the column is still missing on reinspection', async () => {
  const duplicate = Object.assign(new Error('duplicate column name: ordinary_run_marker'), { code: 'SQLITE_ERROR' });
  const query = vi.fn(async () => []);
  const exec = vi.fn(async () => { throw duplicate; });
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  await expect(migration.migrate!({ dbType: 'sqlite', query, exec } as unknown as IDatabase)).rejects.toBe(duplicate);
  expect(query).toHaveBeenCalledTimes(2);
});


it.each([
  Object.assign(new Error('connection lost'), { code: 'PROTOCOL_CONNECTION_LOST' }),
  new Error('ALTER command denied'),
  Object.assign(new Error('database is read-only'), { code: 'SQLITE_READONLY' }),
])('does not reinterpret other ALTER failures as an optional capability denial: %s', async error => {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  const db = { dbType: 'sqlite', query: async () => [], exec: async () => { throw error; } } as unknown as IDatabase;
  await expect(migration.migrate!(db)).rejects.toBe(error);
});

it('does not suppress an inspection failure after a duplicate-column error', async () => {
  const duplicate = Object.assign(new Error('duplicate column name: ordinary_run_marker'), { code: 'SQLITE_ERROR' });
  const disconnected = new Error('connection lost during inspection');
  const query = vi.fn().mockResolvedValueOnce([]).mockRejectedValueOnce(disconnected);
  const db = { dbType: 'sqlite', query, exec: async () => { throw duplicate; } } as unknown as IDatabase;
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  await expect(migration.migrate!(db)).rejects.toBe(disconnected);
});

it('reinspects MySQL after an explicit duplicate column and accepts only a confirmed column', async () => {
  const duplicate = Object.assign(new Error("Duplicate column name 'ordinary_run_marker'"), { code: 'ER_DUP_FIELDNAME', errno: 1060 });
  const compatible = (name: string) => ({ name, data_type: 'varchar', max_length: 64, is_nullable: 'YES', column_default: null });
  const query = vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce([compatible('ordinary_run_marker')])
    .mockResolvedValueOnce([compatible('parent_run_marker'), compatible('child_run_marker')])
    .mockResolvedValueOnce([compatible('parent_run_marker'), compatible('child_run_marker')]);
  const db = { dbType: 'mysql', query, exec: async () => { throw duplicate; } } as unknown as IDatabase;
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  await expect(migration.migrate!(db)).resolves.toBeUndefined();
  expect(query).toHaveBeenCalledTimes(4);
});


it.each([
  Object.assign(new Error('connection lost'), { code: 'PROTOCOL_CONNECTION_LOST' }),
  Object.assign(new Error('disk full'), { code: 'SQLITE_FULL' }),
  new Error('CREATE command denied'),
])('propagates v150 CREATE failures that are not explicit permission denials: %s', async error => {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 150)!;
  const db = { dbType: 'sqlite', dialect: sqliteDialect,
    exec: async () => { throw error; } } as unknown as IDatabase;
  await expect(migration.migrate!(db)).rejects.toBe(error);
});


it('defers when a duplicate-column race created an incompatible SQLite marker', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-bad-race-'));
  const raw = new Database(join(dir, 'db.sqlite'));
  const peer = new Database(join(dir, 'db.sqlite'));
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY);`);
  const real = new SqliteDatabase(raw);
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real),
    transaction: real.transaction.bind(real), close: real.close.bind(real),
    exec: async (sql, params) => {
      if (sql.startsWith('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker'))
        peer.exec("ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker VARCHAR(64) DEFAULT 'bad'");
      return real.exec(sql, params);
    },
  };
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { await db.close(); peer.close(); rmSync(dir, { recursive: true, force: true }); }
});

it.each([
  { name: 'ordinary_run_marker', data_type: 'varchar', max_length: 64, is_nullable: 'NO', column_default: null },
  { name: 'ordinary_run_marker', data_type: 'varchar', max_length: 64, is_nullable: 'YES', column_default: 'bad' },
  { name: 'ordinary_run_marker', data_type: 'varchar', max_length: 63, is_nullable: 'YES', column_default: null },
  { name: 'ordinary_run_marker', data_type: 'text', max_length: 65535, is_nullable: 'YES', column_default: null },
])('rejects incompatible existing MySQL marker metadata %#', async column => {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  const db = { dbType: 'mysql', query: async () => [column], exec: vi.fn() } as unknown as IDatabase;
  await expect(migration.migrate!(db)).rejects.toThrow(/incompatible/i);
  expect(db.exec).not.toHaveBeenCalled();
});

it('rejects incompatible MySQL marker found after duplicate-column race', async () => {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  const duplicate = Object.assign(new Error('Duplicate column'), { code: 'ER_DUP_FIELDNAME', errno: 1060 });
  const query = vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce([
    { name: 'ordinary_run_marker', data_type: 'varchar', max_length: 8, is_nullable: 'YES', column_default: null },
  ]);
  const db = { dbType: 'mysql', query, exec: async () => { throw duplicate; } } as unknown as IDatabase;
  await expect(migration.migrate!(db)).rejects.toThrow(/incompatible/i);
  expect(query).toHaveBeenCalledTimes(2);
});

it.each(['char', 'varchar'])('accepts a nullable MySQL %s(64) marker without a non-null default', async data_type => {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  const names: Record<string, string[]> = { flow_runs: ['ordinary_run_marker'], ordinary_run_requests: ['parent_run_marker', 'child_run_marker'] };
  const query = vi.fn(async (_sql: string, params: unknown[]) => names[String(params[0])].map(name =>
    ({ name, data_type, max_length: 64, is_nullable: 'YES', column_default: null })));
  const exec = vi.fn();
  await expect(migration.migrate!({ dbType: 'mysql', query, exec } as unknown as IDatabase)).resolves.toBeUndefined();
  expect(exec).not.toHaveBeenCalled();
});

it('propagates a connection failure inspecting an existing marker', async () => {
  const error = Object.assign(new Error('connection lost during metadata read'), { code: 'PROTOCOL_CONNECTION_LOST' });
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  const db = { dbType: 'mysql', query: async () => { throw error; }, exec: vi.fn() } as unknown as IDatabase;
  await expect(migration.migrate!(db)).rejects.toBe(error);
  expect(db.exec).not.toHaveBeenCalled();
});


const sqliteMarkerNames = ['ordinary_run_marker', 'parent_run_marker', 'child_run_marker'] as const;
it.each(sqliteMarkerNames.flatMap(name => ['INTEGER PRIMARY KEY', 'INTEGER', 'NUMERIC', 'REAL', 'BLOB', 'CHARINT'].map(definition => ({ name, definition }))))
  ('defers SQLite $name with incompatible $definition and preserves existing runs', async ({ name, definition }) => {
    const raw = new Database(':memory:');
    const marker = (key: string) => `${key} ${key === name ? definition : 'VARCHAR(64)'}`;
    const requestKey = name !== 'ordinary_run_marker' && definition === 'INTEGER PRIMARY KEY' ? 'TEXT NOT NULL UNIQUE' : 'TEXT NOT NULL PRIMARY KEY';
    raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
      CREATE TABLE flow_runs (flow_id TEXT NOT NULL UNIQUE, ${marker('ordinary_run_marker')});
      INSERT INTO flow_runs (flow_id) VALUES ('existing');
      CREATE TABLE ordinary_run_requests (request_key_hash ${requestKey}, parent_flow_id TEXT NOT NULL,
        child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
        ${marker('parent_run_marker')}, ${marker('child_run_marker')});`);
    const db = new SqliteDatabase(raw);
    try {
      await runMigrations(db, 'sqlite');
      expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
      expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    } finally { await db.close(); }
  });

it('defers a duplicate-column race when a peer adds a non-text marker', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-affinity-race-'));
  const raw = new Database(join(dir, 'db.sqlite'));
  const peer = new Database(join(dir, 'db.sqlite'));
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY);`);
  const real = new SqliteDatabase(raw);
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real),
    transaction: real.transaction.bind(real), close: real.close.bind(real),
    exec: async (sql, params) => {
      if (sql.startsWith('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker')) peer.exec('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker NUMERIC');
      return real.exec(sql, params);
    },
  };
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { await db.close(); peer.close(); rmSync(dir, { recursive: true, force: true }); }
});

it('retries SQLite migration after a non-text marker is repaired', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version,description) VALUES (150,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT, ordinary_run_marker NUMERIC);
    INSERT INTO flow_runs (flow_id) VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY, parent_run_marker TEXT, child_run_marker VARCHAR(64));`);
  const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker; ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker TEXT');
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { await db.close(); }
});

it.each([
  ['missing column', 'child_workflow_id'],
  ['nullable key', 'request_key_hash'],
  ['nullable fingerprint', 'request_fingerprint'],
  ['missing unique child index', 'index'],
])('defers v150 for existing SQLite storage with %s and retries without losing data', async (_label, defect) => {
  const raw = new Database(':memory:');
  const columns = [
    `request_key_hash TEXT ${defect === 'request_key_hash' ? '' : 'NOT NULL'} PRIMARY KEY`,
    'parent_flow_id TEXT NOT NULL',
    `child_flow_id TEXT NOT NULL ${defect === 'index' ? '' : 'UNIQUE'}`,
    ...(defect === 'child_workflow_id' ? [] : ['child_workflow_id TEXT NOT NULL']),
    `request_fingerprint TEXT ${defect === 'request_fingerprint' ? '' : 'NOT NULL'}`,
  ];
  raw.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT); INSERT INTO schema_version (version, description) VALUES (149,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT NOT NULL UNIQUE); INSERT INTO flow_runs (flow_id) VALUES ('existing');
    CREATE TABLE ordinary_run_requests (${columns.join(',')});
    INSERT INTO ordinary_run_requests (request_key_hash,parent_flow_id,child_flow_id,request_fingerprint${defect === 'child_workflow_id' ? '' : ',child_workflow_id'}) VALUES ('key','parent','child','hash'${defect === 'child_workflow_id' ? '' : ",'workflow'"});`);
  const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    expect(raw.prepare('SELECT request_key_hash,parent_flow_id,child_flow_id,request_fingerprint FROM ordinary_run_requests').all())
      .toEqual([{ request_key_hash: 'key', parent_flow_id: 'parent', child_flow_id: 'child', request_fingerprint: 'hash' }]);
    raw.exec(`ALTER TABLE ordinary_run_requests RENAME TO old_requests;
      CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
        child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);
      INSERT INTO ordinary_run_requests SELECT request_key_hash,parent_flow_id,child_flow_id,${defect === 'child_workflow_id' ? "'workflow'" : 'child_workflow_id'},request_fingerprint FROM old_requests; DROP TABLE old_requests;`);
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    expect(raw.prepare('SELECT request_key_hash,parent_flow_id,child_flow_id,child_workflow_id,request_fingerprint FROM ordinary_run_requests').all())
      .toEqual([{ request_key_hash: 'key', parent_flow_id: 'parent', child_flow_id: 'child', child_workflow_id: 'workflow', request_fingerprint: 'hash' }]);
  } finally { await db.close(); }
});

it('propagates SQLite v150 metadata read failures instead of reporting incompatibility', async () => {
  const error = new Error('metadata connection lost');
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 150)!;
  const db = { dbType: 'sqlite', dialect: sqliteDialect, exec: async () => ({ affectedRows: 0 }),
    query: async () => { throw error; } } as unknown as IDatabase;
  await expect(migration.migrate!(db)).rejects.toBe(error);
});

it.each(['missing', 'nullable', 'narrow', 'type', 'engine', 'nonunique', 'prefix', 'composite'])
  ('rejects incompatible MySQL v150 metadata: %s', async defect => {
    const columns = [
      ['request_key_hash', 64], ['parent_flow_id', 128], ['child_flow_id', 128],
      ['child_workflow_id', 128], ['request_fingerprint', 64],
    ].map(([name, length]) => ({ column_name: name, data_type: 'varchar', max_length: length, is_nullable: 'NO' }));
    if (defect === 'missing') columns.pop();
    if (defect === 'nullable') columns[0].is_nullable = 'YES';
    if (defect === 'narrow') columns[1].max_length = 127;
    if (defect === 'type') columns[2].data_type = 'text';
    const indexes = [
      { index_name: 'PRIMARY', column_name: 'request_key_hash', non_unique: 0, sub_part: null as number | null, seq: 1 },
      { index_name: 'child', column_name: 'child_flow_id', non_unique: 0, sub_part: null as number | null, seq: 1 },
    ];
    if (defect === 'nonunique') indexes[1].non_unique = 1;
    if (defect === 'prefix') indexes[1].sub_part = 8;
    if (defect === 'composite') indexes.push({ ...indexes[1], column_name: 'parent_flow_id', seq: 2 });
    const db = { dbType: 'mysql', dialect: (await import('../db/dialect.js')).mysqlDialect,
      exec: vi.fn(async () => ({ affectedRows: 0 })),
      query: async (sql: string) => sql.includes('information_schema.TABLES') ? [{ engine: defect === 'engine' ? 'MyISAM' : 'InnoDB' }]
        : sql.includes('information_schema.COLUMNS') ? columns : indexes } as unknown as IDatabase;
    const migration = ordinaryRunRequestMigrations.find(m => m.version === 150)!;
    await expect(migration.migrate!(db)).rejects.toThrow(/incompatible/i);
  });

it.each(['NUMERIC', 'INTEGER', 'BLOB', 'CHARINT'])('defers v150 for a SQLite hash column with %s affinity', async type => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash ${type} NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);`);
  const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
  } finally { await db.close(); }
});

it.each(['partial', 'composite'])('defers v150 for SQLite %s child index', async index => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);
    CREATE UNIQUE INDEX child_idx ON ordinary_run_requests (${index === 'composite' ? 'child_flow_id, parent_flow_id' : 'child_flow_id'}) ${index === 'partial' ? "WHERE child_flow_id <> ''" : ''};`);
  const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
  } finally { await db.close(); }
});

it('accepts a SQLite unique index whose name contains a quote', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);
    CREATE UNIQUE INDEX "child""index" ON ordinary_run_requests(child_flow_id);`);
  const db = new SqliteDatabase(raw);
  try { await runMigrations(db, 'sqlite'); expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 }); }
  finally { await db.close(); }
});

it('defers v150 for an extra required SQLite column without default and preserves rows', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL, extra TEXT NOT NULL);
    INSERT INTO ordinary_run_requests VALUES ('k','p','c','w','f','keep');`);
  const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    expect(raw.prepare('SELECT extra FROM ordinary_run_requests').get()).toEqual({ extra: 'keep' });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').get()).toEqual({ flow_id: 'existing' });
  } finally { await db.close(); }
});

it.each(['VIRTUAL GENERATED', 'STORED GENERATED'])('rejects MySQL %s marker on v151', async extra => {
  const migration = ordinaryRunRequestMigrations.find(m => m.version === 151)!;
  const query = vi.fn(async () => [{ name: 'ordinary_run_marker', data_type: 'varchar', max_length: 64,
    is_nullable: 'YES', column_default: null, extra, generation_expression: "'constant'" }]);
  await expect(migration.migrate!({ dbType: 'mysql', query, exec: vi.fn() } as unknown as IDatabase)).rejects.toThrow(/incompatible/i);
});

it('defers v151 when a SQLite marker is generated rather than writable', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT, ordinary_run_marker TEXT GENERATED ALWAYS AS (NULL) VIRTUAL);
    INSERT INTO flow_runs (flow_id) VALUES ('existing'); CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY);`);
  const db = new SqliteDatabase(raw);
  try { await runMigrations(db, 'sqlite'); expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 }); }
  finally { await db.close(); }
});

it.each(['SQLITE_BUSY', 'SQLITE_READONLY', 'SQLITE_BUSY_SNAPSHOT', 'SQLITE_READONLY_DBMOVED'])
  ('defers optional SQLite DDL on %s, retains readable business data, and retries', async code => {
    const raw = new Database(':memory:');
    raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
      CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');`);
    seedBaseTriggers(raw);
  const real = new SqliteDatabase(raw);
    let fail = true;
    const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real),
      transaction: real.transaction.bind(real), exec: async (sql, params) => {
        if (fail && sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) throw Object.assign(new Error(code), { code });
        return real.exec(sql, params);
      } };
    try {
      await runMigrations(db, 'sqlite');
      expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
      expect(await real.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
      fail = false; await runMigrations(db, 'sqlite');
      expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    } finally { await db.close(); }
  });

it('does not hide a database failure while checking availability after a deferred SQLite migration', async () => {
  const raw = new Database(':memory:');
  raw.exec("CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old'); CREATE TABLE flow_runs (flow_id TEXT)");
  const real = new SqliteDatabase(raw); let failed = false;
  const unavailable = Object.assign(new Error('database I/O failed'), { code: 'SQLITE_IOERR' });
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, close: real.close.bind(real), transaction: real.transaction.bind(real),
    exec: async (sql, params) => { if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) { failed = true; throw Object.assign(new Error('busy'), { code: 'SQLITE_BUSY' }); } return real.exec(sql, params); },
    query: async <T>(sql: string, params?: unknown[]) => { if (failed) throw unavailable; return real.query<T>(sql, params); } };
  try { await expect(runMigrations(db, 'sqlite')).rejects.toBe(unavailable); } finally { await db.close(); }
});

it('defers v150 when an explicitly supplied SQLite identifier is generated', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL,
      request_fingerprint TEXT GENERATED ALWAYS AS ('fixed') VIRTUAL NOT NULL);`);
  const db = new SqliteDatabase(raw);
  try { await runMigrations(db, 'sqlite'); expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 }); }
  finally { await db.close(); }
});

it.each(['ALTER', 'ledger'])('defers SQLite v151 on READONLY during %s and retries', async where => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT PRIMARY KEY);`);
  seedBaseTriggers(raw);
  const real = new SqliteDatabase(raw); let fail = true;
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real),
    transaction: real.transaction.bind(real), exec: async (sql, params) => {
      if (fail && (where === 'ALTER' ? sql.startsWith('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker')
        : sql.startsWith('INSERT INTO schema_version') && params?.[0] === 151))
        throw Object.assign(new Error('read only'), { code: 'SQLITE_READONLY' });
      return real.exec(sql, params);
    } };
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    expect(await db.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
    fail = false; await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
  } finally { await db.close(); }
});

it('propagates SQLite IOERR from optional DDL instead of deferring it', async () => {
  const raw = new Database(':memory:');
  raw.exec("CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old'); CREATE TABLE flow_runs (flow_id TEXT)");
  const real = new SqliteDatabase(raw); const error = Object.assign(new Error('disk failed'), { code: 'SQLITE_IOERR' });
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real), transaction: real.transaction.bind(real),
    exec: async (sql, params) => { if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) throw error; return real.exec(sql, params); } };
  try { await expect(runMigrations(db, 'sqlite')).rejects.toBe(error); } finally { await db.close(); }
});

it('rejects initDatabase on SQLite v150 READONLY and retries without a close', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { initDatabase, closeDatabase } = await import('../db.js');
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-init-readonly-')); const path = join(dir, 'db.sqlite');
  const seed = new Database(path);
  seed.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (149,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT); INSERT INTO flow_runs (flow_id) VALUES ('existing');`);
  seedBaseTriggers(seed);
  seed.close();
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  const original = SqliteDatabase.prototype.exec;
  let failure: unknown;
  const spy = vi.spyOn(SqliteDatabase.prototype, 'exec').mockImplementation(async function (this: SqliteDatabase, sql, params) {
    if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
      (this as unknown as { db: Database.Database }).db.pragma('query_only = ON');
      try { return await original.call(this, sql, params); } catch (error) { failure = error; throw error; }
    }
    return original.call(this, sql, params);
  });
  try {
    await expect(initDatabase()).rejects.toThrow(/migrations deferred.*incomplete/i);
    expect((failure as { code?: string }).code).toBe('SQLITE_READONLY');
    const inspect = new Database(path, { readonly: true });
    try {
      expect(inspect.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
      expect(inspect.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    } finally { inspect.close(); }
    spy.mockRestore();
    const repaired = await initDatabase();
    expect(repaired.db.dbType).toBe('sqlite');
    expect(await repaired.db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 151 }]);
    expect(await repaired.db.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
  } finally { spy.mockRestore(); await closeDatabase(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true }); }
});

it('permits a SQLite extra required column with a usable default', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL, extra TEXT NOT NULL DEFAULT 'ok');`);
  const db = new SqliteDatabase(raw);
  try { await runMigrations(db, 'sqlite'); expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 }); }
  finally { await db.close(); }
});

it('defers a real SQLite lock encountered by v150 and resumes after release', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-init-busy-')); const path = join(dir, 'db.sqlite');
  const raw = new Database(path); const peer = new Database(path);
  raw.pragma('journal_mode = WAL'); raw.pragma('busy_timeout = 1');
  raw.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (149,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT); INSERT INTO flow_runs (flow_id) VALUES ('existing');`);
  seedBaseTriggers(raw);
  const real = new SqliteDatabase(raw); let locked = false; let code: unknown;
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real), transaction: real.transaction.bind(real),
    exec: async (sql, params) => {
      if (!locked && sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) { peer.exec('BEGIN IMMEDIATE'); locked = true; }
      try { return await real.exec(sql, params); } catch (error) { if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) code = (error as { code?: unknown }).code; throw error; }
    } };
  try {
    await runMigrations(db, 'sqlite');
    expect(code).toBe('SQLITE_BUSY');
    expect(await real.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    peer.exec('ROLLBACK'); await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
  } finally { if (peer.inTransaction) peer.exec('ROLLBACK'); await db.close(); peer.close(); rmSync(dir, { recursive: true, force: true }); }
});

it('rejects SQLite initialization when a peer records v150 before a busy ledger insert', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { initDatabase, closeDatabase } = await import('../db.js');
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-ledger-race-')); const path = join(dir, 'db.sqlite');
  const peer = new Database(path); peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (149,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT); INSERT INTO flow_runs (flow_id) VALUES ('existing');`);
  seedBaseTriggers(peer);
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  const original = SqliteDatabase.prototype.exec; let code: unknown;
  const spy = vi.spyOn(SqliteDatabase.prototype, 'exec').mockImplementation(async function (this: SqliteDatabase, sql, params) {
    if (sql.startsWith('INSERT INTO schema_version') && params?.[0] === 150) {
      peer.prepare('INSERT INTO schema_version (version, description) VALUES (?, ?)').run(...params);
      peer.exec('BEGIN IMMEDIATE');
      (this as unknown as { db: Database.Database }).db.pragma('busy_timeout = 1');
      try { return await original.call(this, sql, params); } catch (error) { code = (error as { code?: unknown }).code; throw error; }
    }
    return original.call(this, sql, params);
  });
  try {
    await expect(initDatabase()).rejects.toThrow(/migrations deferred.*incomplete/i);
    expect(code).toBe('SQLITE_BUSY');
    expect(peer.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    expect(peer.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    expect(peer.prepare('PRAGMA table_xinfo(flow_runs)').all()).not.toEqual(expect.arrayContaining([expect.objectContaining({ name: 'ordinary_run_marker' })]));
    spy.mockRestore(); peer.exec('ROLLBACK');
    const retried = await initDatabase();
    expect(await retried.db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 151 }]);
  } finally { spy.mockRestore(); if (peer.inTransaction) peer.exec('ROLLBACK'); await closeDatabase(); peer.close(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true }); }
});

it.each(['wrong description', 'missing version', 'schema incompatible', 'metadata failure', 'readonly', 'io error'])
  ('does not accept an ambiguous concurrent v150 ledger result: %s', async scenario => {
    const raw = new Database(':memory:');
    raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
      CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');`);
    const real = new SqliteDatabase(raw); let inserted = false;
    const code = scenario === 'readonly' ? 'SQLITE_READONLY' : scenario === 'io error' ? 'SQLITE_IOERR' : 'SQLITE_BUSY';
    const writeError = Object.assign(new Error('ledger write failed'), { code });
    const readError = Object.assign(new Error('metadata failed'), { code: 'SQLITE_IOERR' });
    const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, close: real.close.bind(real), transaction: real.transaction.bind(real),
      query: async <T>(sql: string, params?: unknown[]) => {
        if (inserted && scenario === 'metadata failure' && sql.startsWith('PRAGMA table_xinfo(ordinary_run_requests)')) throw readError;
        return real.query<T>(sql, params);
      },
      exec: async (sql, params) => {
        if (sql.startsWith('INSERT INTO schema_version') && params?.[0] === 150) {
          raw.prepare('INSERT INTO schema_version VALUES (?, ?)').run(scenario === 'missing version' ? 151 : 150,
            scenario === 'wrong description' ? 'unrelated' : params[1]);
          if (scenario === 'schema incompatible') raw.exec('ALTER TABLE ordinary_run_requests RENAME COLUMN parent_flow_id TO wrong');
          inserted = true; throw writeError;
        }
        return real.exec(sql, params);
      } };
    try {
      if (scenario === 'schema incompatible') await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/incompatible/i);
      else if (scenario === 'metadata failure') await expect(runMigrations(db, 'sqlite')).rejects.toBe(readError);
      else await expect(runMigrations(db, 'sqlite')).rejects.toBe(writeError);
      expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
      expect(raw.prepare('PRAGMA table_xinfo(flow_runs)').all()).not.toEqual(expect.arrayContaining([expect.objectContaining({ name: 'ordinary_run_marker' })]));
    } finally { await db.close(); }
  });

it('stops after a confirmed concurrent v151 ledger write without executing v152', async () => {
  const { migrations } = await import('../schema.js');
  const later = { version: 152, description: 'future migration', sql: ['INSERT INTO future_probe VALUES (1)'] };
  migrations.push(later);
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing'); CREATE TABLE future_probe (value INTEGER);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);`);
  seedBaseTriggers(raw);
  const real = new SqliteDatabase(raw); let raced = false;
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real), transaction: real.transaction.bind(real),
    exec: async (sql, params) => {
      if (!raced && sql.startsWith('INSERT INTO schema_version') && params?.[0] === 151) {
        raced = true; raw.prepare('INSERT INTO schema_version VALUES (?, ?)').run(...params);
        throw Object.assign(new Error('busy'), { code: 'SQLITE_BUSY' });
      }
      return real.exec(sql, params);
    } };
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    expect(raw.prepare('SELECT * FROM future_probe').all()).toEqual([]);
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { migrations.pop(); await db.close(); }
});

it('does not accept a peer v151 ledger row when a marker is missing', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);`);
  const real = new SqliteDatabase(raw);
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real), transaction: real.transaction.bind(real),
    exec: async (sql, params) => {
      if (sql.startsWith('INSERT INTO schema_version') && params?.[0] === 151) {
        raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker');
        raw.prepare('INSERT INTO schema_version VALUES (?, ?)').run(...params);
        throw Object.assign(new Error('busy'), { code: 'SQLITE_BUSY' });
      }
      return real.exec(sql, params);
    } };
  try {
    await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/incompatible.*marker/i);
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { await db.close(); }
});

it('rejects SQLite initialization when a peer completes v151 before a busy marker DDL', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { initDatabase, closeDatabase } = await import('../db.js');
  const { migrations } = await import('../schema.js');
  const later = { version: 152, description: 'future migration', sql: ['INSERT INTO future_probe VALUES (1)'] };
  migrations.push(later);
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-ddl-ledger-race-')); const path = join(dir, 'db.sqlite');
  const peer = new Database(path); peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (150,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT); INSERT INTO flow_runs (flow_id) VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);
    CREATE TABLE future_probe (value INTEGER);`);
  seedBaseTriggers(peer);
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  const original = SqliteDatabase.prototype.exec; let code: unknown;
  const spy = vi.spyOn(SqliteDatabase.prototype, 'exec').mockImplementation(async function (this: SqliteDatabase, sql, params) {
    if (sql.startsWith('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker')) {
      peer.exec(`ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker VARCHAR(64) DEFAULT NULL;
        ALTER TABLE ordinary_run_requests ADD COLUMN parent_run_marker VARCHAR(64) DEFAULT NULL;
        ALTER TABLE ordinary_run_requests ADD COLUMN child_run_marker VARCHAR(64) DEFAULT NULL;
        INSERT INTO schema_version (version,description) VALUES (151,'Bind ordinary request associations to run incarnations');`);
      peer.exec('BEGIN IMMEDIATE');
      (this as unknown as { db: Database.Database }).db.pragma('busy_timeout = 1');
      try { return await original.call(this, sql, params); } catch (error) { code = (error as { code?: unknown }).code; throw error; }
    }
    return original.call(this, sql, params);
  });
  try {
    await expect(initDatabase()).rejects.toThrow(/migrations deferred.*incomplete/i);
    expect(code).toBe('SQLITE_BUSY');
    expect(peer.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    expect(peer.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    expect(peer.prepare('SELECT * FROM future_probe').all()).toEqual([]);
    spy.mockRestore(); peer.exec('ROLLBACK');
    const retried = await initDatabase();
    expect(await retried.db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 152 }]);
    expect(await retried.db.query('SELECT * FROM future_probe')).toEqual([{ value: 1 }]);
  } finally { migrations.pop(); spy.mockRestore(); if (peer.inTransaction) peer.exec('ROLLBACK'); await closeDatabase(); peer.close(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true }); }
});

it.each(['wrong description', 'conflicting descriptions', 'missing marker', 'incompatible marker', 'incompatible v150', 'metadata failure', 'higher version', 'readonly', 'io error'])
  ('does not accept a concurrent v151 DDL result with %s', async scenario => {
    const raw = new Database(':memory:');
    raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
      CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');
      CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
        child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);`);
    const real = new SqliteDatabase(raw); let raced = false;
    const code = scenario === 'readonly' ? 'SQLITE_READONLY' : scenario === 'io error' ? 'SQLITE_IOERR' : 'SQLITE_BUSY';
    const error = Object.assign(new Error('marker DDL failed'), { code });
    const metadataError = Object.assign(new Error('metadata failed'), { code: 'SQLITE_IOERR' });
    const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, close: real.close.bind(real), transaction: real.transaction.bind(real),
      query: async <T>(sql: string, params?: unknown[]) => {
        if (raced && scenario === 'metadata failure' && sql === 'PRAGMA table_xinfo(ordinary_run_requests)') throw metadataError;
        return real.query<T>(sql, params);
      },
      exec: async (sql, params) => {
        if (sql.startsWith('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker')) {
          raw.exec(`ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker ${scenario === 'incompatible marker' ? 'NUMERIC' : 'TEXT'};
            ALTER TABLE ordinary_run_requests ADD COLUMN parent_run_marker TEXT;
            ${scenario === 'missing marker' ? '' : 'ALTER TABLE ordinary_run_requests ADD COLUMN child_run_marker TEXT;'}
            ${scenario === 'incompatible v150' ? 'ALTER TABLE ordinary_run_requests RENAME COLUMN parent_flow_id TO wrong;' : ''}`);
          const description = scenario === 'wrong description' ? 'wrong' : 'Bind ordinary request associations to run incarnations';
          raw.prepare('INSERT INTO schema_version VALUES (?, ?)').run(scenario === 'higher version' ? 152 : 151, description);
          if (scenario === 'conflicting descriptions') raw.prepare('INSERT INTO schema_version VALUES (?, ?)').run(151, 'wrong');
          raced = true; throw error;
        }
        return real.exec(sql, params);
      } };
    try {
      if (scenario === 'metadata failure') await expect(runMigrations(db, 'sqlite')).rejects.toBe(metadataError);
      else if (['missing marker', 'incompatible marker', 'incompatible v150'].includes(scenario))
        await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/incompatible/i);
      else await expect(runMigrations(db, 'sqlite')).rejects.toBe(error);
      expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    } finally { await db.close(); }
  });

it('defers v150 CREATE BUSY after a peer records the matching table and ledger row', async () => {
  const { ordinaryRunRequestDdl } = await import('../schema-ordinary-run-request.js');
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');`);
  seedBaseTriggers(raw);
  const real = new SqliteDatabase(raw); let raced = false;
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), close: real.close.bind(real),
    transaction: real.transaction.bind(real), exec: async (sql, params) => {
      if (!raced && sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
        raced = true;
        raw.exec(sqliteDialect.renderDdl(ordinaryRunRequestDdl));
        raw.prepare('INSERT INTO schema_version VALUES (?, ?)').run(150, 'Store idempotent ordinary run request associations');
        throw Object.assign(new Error('busy'), { code: 'SQLITE_BUSY' });
      }
      return real.exec(sql, params);
    } };
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    expect(raw.prepare('PRAGMA table_xinfo(flow_runs)').all()).not.toEqual(expect.arrayContaining([expect.objectContaining({ name: 'ordinary_run_marker' })]));
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { await db.close(); }
});
