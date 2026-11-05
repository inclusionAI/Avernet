import { expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, closeDatabase, initDatabase, runMigrations, type IDatabase } from '../db.js';
import { ordinaryRunRequestDdl } from '../schema-ordinary-run-request.js';
import { sqliteDialect } from '../db/dialect.js';

const v150 = 'Store idempotent ordinary run request associations';
const v151 = 'Bind ordinary request associations to run incarnations';

it('rejects a v150 ledger BUSY race and retries when a peer has completed v151', async () => {
  const { mkdtempSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { migrations, sqliteTriggers } = await import('../schema.js');
  const later = { version: 152, description: 'future migration', sql: ['INSERT INTO future_probe VALUES (1)'] };
  const dir = mkdtempSync(join(tmpdir(), 'ordinary-advanced-busy-')); const path = join(dir, 'db.sqlite');
  const peer = new Database(path); peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version,description) VALUES (149,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT);
    INSERT INTO flow_runs (gmt_modified,flow_id) VALUES (0,'existing');
    CREATE TABLE future_probe (value INTEGER);`);
  for (const sql of sqliteTriggers.filter(ddl => /trg_(flow_runs|schema_version)_update\b/.test(ddl))) peer.exec(sql);
  migrations.push(later);
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  let code: unknown; let triggerAttempts = 0;
  const original = SqliteDatabase.prototype.exec;
  const spy = vi.spyOn(SqliteDatabase.prototype, 'exec').mockImplementation(async function (this: SqliteDatabase, sql, params) {
    if (/^CREATE TRIGGER/i.test(sql)) triggerAttempts++;
    if (sql.startsWith('INSERT INTO schema_version') && params?.[0] === 150) {
      peer.prepare('INSERT INTO schema_version (version,description) VALUES (?,?)').run(150, v150);
      peer.exec(`ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker TEXT;`);
      peer.prepare('INSERT INTO schema_version (version,description) VALUES (?,?)').run(151, v151);
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
    expect(triggerAttempts).toBe(0);
    expect(peer.prepare("SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_flow_runs_update'").get()).toEqual({ name: 'trg_flow_runs_update' });
    spy.mockRestore(); peer.exec('ROLLBACK');
    const retried = await initDatabase();
    expect(await retried.db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 152 }]);
    expect(await retried.db.query('SELECT * FROM future_probe')).toEqual([{ value: 1 }]);
    expect(await retried.db.query("SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_flow_runs_update'"))
      .toEqual([{ name: 'trg_flow_runs_update' }]);
    await retried.db.exec("UPDATE flow_runs SET flow_id = 'changed' WHERE flow_id = 'existing'");
    expect((await retried.db.query<{ gmt_modified: number }>('SELECT gmt_modified FROM flow_runs'))[0].gmt_modified).toBeGreaterThan(0);
  } finally {
    migrations.pop(); spy.mockRestore(); if (peer.inTransaction) peer.exec('ROLLBACK');
    await closeDatabase(); peer.close(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true });
  }
});

it.each(['missing v150', 'wrong v150', 'wrong v151', 'conflicting v150', 'conflicting v151', 'bad v150 schema', 'missing marker', 'bad marker', 'metadata error', 'unknown version', 'readonly', 'busy snapshot', 'io error'])
  ('fails closed for an advanced peer with %s', async scenario => {
    const raw = new Database(':memory:');
    raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
      CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');`);
    const real = new SqliteDatabase(raw); let raced = false;
    const code = scenario === 'readonly' ? 'SQLITE_READONLY' : scenario === 'busy snapshot' ? 'SQLITE_BUSY_SNAPSHOT'
      : scenario === 'io error' ? 'SQLITE_IOERR' : 'SQLITE_BUSY';
    const error = Object.assign(new Error('v150 failed'), { code });
    const metadataError = Object.assign(new Error('metadata failure'), { code: 'SQLITE_IOERR' });
    const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, transaction: real.transaction.bind(real), close: real.close.bind(real),
      query: async <T>(sql: string, params?: unknown[]) => {
        if (raced && scenario === 'metadata error' && sql.includes('SELECT description') && params?.[0] === 151) throw metadataError;
        return real.query<T>(sql, params);
      },
      exec: async (sql, params) => {
        if (!raced && sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
          raw.exec(sqliteDialect.renderDdl(ordinaryRunRequestDdl));
          raw.exec(`ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker ${scenario === 'bad marker' ? 'NUMERIC' : 'TEXT'};`);
          if (scenario === 'missing marker') raw.exec('ALTER TABLE ordinary_run_requests DROP COLUMN child_run_marker');
          if (scenario === 'bad v150 schema') raw.exec('ALTER TABLE ordinary_run_requests RENAME COLUMN parent_flow_id TO wrong');
          if (scenario !== 'missing v150') raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(150, scenario === 'wrong v150' ? 'wrong' : v150);
          if (scenario === 'conflicting v150') raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(150, 'wrong');
          raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(151, scenario === 'wrong v151' ? 'wrong' : v151);
          if (scenario === 'conflicting v151') raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(151, 'wrong');
          if (scenario === 'unknown version') raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(152, 'unknown');
          raced = true; throw error;
        }
        return real.exec(sql, params);
      } };
    try {
      if (scenario === 'metadata error') await expect(runMigrations(db, 'sqlite')).rejects.toBe(metadataError);
      else if (scenario === 'missing marker' || scenario === 'bad marker' || scenario === 'bad v150 schema') await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/incompatible/i);
      else await expect(runMigrations(db, 'sqlite')).rejects.toBe(error);
      expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    } finally { await db.close(); }
  });

it('accepts v150 CREATE BUSY when the peer has verified and recorded both known versions', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT); INSERT INTO flow_runs VALUES ('existing');`);
  const { sqliteTriggers } = await import('../schema.js');
  for (const ddl of sqliteTriggers.filter(sql => /trg_(flow_runs|schema_version)_update\b/.test(sql))) raw.exec(ddl);
  const real = new SqliteDatabase(raw); let raced = false;
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real), transaction: real.transaction.bind(real), close: real.close.bind(real),
    exec: async (sql, params) => {
      if (!raced && sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
        raw.exec(sqliteDialect.renderDdl(ordinaryRunRequestDdl));
        raw.exec('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker TEXT');
        raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(150, v150);
        raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(151, v151);
        raced = true; throw Object.assign(new Error('busy'), { code: 'SQLITE_BUSY' });
      }
      return real.exec(sql, params);
    } };
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
  } finally { await real.close(); }
});

it('fails closed when an unknown version appears during concurrent ledger verification', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT);`);
  const real = new SqliteDatabase(raw); let raced = false; let maxReads = 0;
  const error = Object.assign(new Error('v150 busy'), { code: 'SQLITE_BUSY' });
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, transaction: real.transaction.bind(real), close: real.close.bind(real),
    query: async <T>(sql: string, params?: unknown[]) => {
      if (raced && sql === 'SELECT MAX(version) as v FROM schema_version' && ++maxReads === 2)
        raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(152, 'unknown');
      return real.query<T>(sql, params);
    },
    exec: async (sql, params) => {
      if (!raced && sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
        raw.exec(sqliteDialect.renderDdl(ordinaryRunRequestDdl));
        raw.exec('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker TEXT');
        raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(150, v150);
        raw.prepare('INSERT INTO schema_version VALUES (?,?)').run(151, v151);
        raced = true; throw error;
      }
      return real.exec(sql, params);
    } };
  try { await expect(runMigrations(db, 'sqlite')).rejects.toBe(error); }
  finally { await real.close(); }
});
