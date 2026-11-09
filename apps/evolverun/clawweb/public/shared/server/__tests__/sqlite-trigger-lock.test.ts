import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations } from '../db.js';
import { sqliteTriggers } from '../schema.js';

const flowTrigger = sqliteTriggers.find(sql => sql.includes('trg_flow_runs_update'))!;

it.each([false, true])('handles a real writer lock with trigger already installed: %s', async installed => {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-trigger-lock-'));
  const path = join(dir, 'test.sqlite');
  const peer = new Database(path);
  peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (149, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES (1, 'existing', 0);`);
  if (installed) peer.exec(flowTrigger);
  peer.exec(sqliteTriggers.find(sql => sql.includes('trg_schema_version_update'))!);
  const raw = new Database(path);
  raw.pragma('busy_timeout = 20');
  const db = new SqliteDatabase(raw);
  const exec = vi.spyOn(db, 'exec');
  try {
    peer.exec('BEGIN IMMEDIATE');
    if (installed) await expect(runMigrations(db, 'sqlite')).resolves.toBeUndefined();
    else await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/trigger.*missing.*deferred/i);
    const triggerWrites = exec.mock.calls.filter(([sql]) => /^CREATE TRIGGER/i.test(sql));
    expect(triggerWrites.length).toBe(0);
    expect(peer.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    expect(peer.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    peer.exec('ROLLBACK');
    await runMigrations(db, 'sqlite');
    expect(peer.prepare("SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_flow_runs_update'").get())
      .toEqual({ name: 'trg_flow_runs_update' });
    peer.exec("UPDATE flow_runs SET flow_id = 'changed' WHERE id = 1");
    expect((peer.prepare('SELECT gmt_modified FROM flow_runs WHERE id = 1').get() as { gmt_modified: number }).gmt_modified).toBeGreaterThan(0);
  } finally {
    if (peer.inTransaction) peer.exec('ROLLBACK');
    await db.close(); peer.close(); rmSync(dir, { recursive: true, force: true });
  }
});

it('rejects an existing trigger with an incompatible definition instead of assuming readiness', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER);
    CREATE TRIGGER trg_flow_runs_update AFTER UPDATE ON flow_runs BEGIN SELECT 1; END;`);
  const db = new SqliteDatabase(raw);
  try { await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/trigger.*trg_flow_runs_update/i); }
  finally { await db.close(); }
});

it('rejects locked missing triggers, closes the connection and repairs on the next initialization', async () => {
  const { closeDatabase, initDatabase } = await import('../db.js');
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-trigger-init-'));
  const path = join(dir, 'test.sqlite');
  const peer = new Database(path);
  peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (149, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES (1, 'existing', 0);`);
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  const original = SqliteDatabase.prototype.exec;
  let failedConnection: Database.Database | undefined;
  let triggerWrites = 0;
  const spy = vi.spyOn(SqliteDatabase.prototype, 'exec').mockImplementation(async function (this: SqliteDatabase, sql, params) {
    if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
      failedConnection = (this as unknown as { db: Database.Database }).db;
      failedConnection.pragma('busy_timeout = 10');
      peer.exec('BEGIN IMMEDIATE');
    }
    if (/^CREATE TRIGGER/i.test(sql)) triggerWrites++;
    return original.call(this, sql, params);
  });
  try {
    await expect(initDatabase()).rejects.toThrow(/trigger.*missing.*deferred/i);
    expect(triggerWrites).toBe(0);
    expect(failedConnection?.open).toBe(false);
    expect(peer.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    expect(peer.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    spy.mockRestore(); peer.exec('ROLLBACK');
    const { db } = await initDatabase();
    expect(db.dbType).toBe('sqlite');
    expect(await db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 151 }]);
    await db.exec("UPDATE flow_runs SET flow_id = 'changed' WHERE id = 1");
    expect((await db.query<{ gmt_modified: number }>('SELECT gmt_modified FROM flow_runs'))[0].gmt_modified).toBeGreaterThan(0);
  } finally {
    spy.mockRestore(); if (peer.inTransaction) peer.exec('ROLLBACK');
    await closeDatabase(); peer.close(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true });
  }
});

it.each(['metadata', 'create'] as const)('propagates trigger %s errors', async phase => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER);`);
  const db = new SqliteDatabase(raw);
  const failure = Object.assign(new Error('I/O failure'), { code: 'SQLITE_IOERR' });
  const originalQuery = db.query.bind(db); const originalExec = db.exec.bind(db);
  vi.spyOn(db, 'query').mockImplementation(async (sql, params) => {
    if (phase === 'metadata' && sql.includes('sqlite_master')) throw failure;
    return originalQuery(sql, params);
  });
  vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
    if (phase === 'create' && sql.startsWith('CREATE TRIGGER')) throw failure;
    return originalExec(sql, params);
  });
  try { await expect(runMigrations(db, 'sqlite')).rejects.toBe(failure); }
  finally { await db.close(); }
});

it('stops at the first trigger write failure even when no optional migration deferred', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-trigger-direct-'));
  const path = join(dir, 'test.sqlite');
  const peer = new Database(path);
  peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER);`);
  const raw = new Database(path); raw.pragma('busy_timeout = 10');
  const db = new SqliteDatabase(raw); const exec = vi.spyOn(db, 'exec');
  try {
    peer.exec('BEGIN IMMEDIATE');
    await expect(runMigrations(db, 'sqlite')).rejects.toMatchObject({ code: 'SQLITE_BUSY' });
    expect(exec.mock.calls.filter(([sql]) => /^CREATE TRIGGER/i.test(sql))).toHaveLength(1);
  } finally {
    if (peer.inTransaction) peer.exec('ROLLBACK');
    await db.close(); peer.close(); rmSync(dir, { recursive: true, force: true });
  }
});

it('detects a conflicting trigger created by a peer between inspection and creation', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-trigger-race-'));
  const path = join(dir, 'test.sqlite');
  const peer = new Database(path); peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER);`);
  const db = new SqliteDatabase(new Database(path)); const original = db.exec.bind(db);
  vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
    if (sql === flowTrigger) peer.exec('CREATE TRIGGER trg_flow_runs_update AFTER UPDATE ON flow_runs BEGIN SELECT 1; END');
    return original(sql, params);
  });
  try { await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/trigger.*trg_flow_runs_update.*incompatible/i); }
  finally { await db.close(); peer.close(); rmSync(dir, { recursive: true, force: true }); }
});

it('accepts the equivalent trigger SQLite rewrites when a table is renamed away and back', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, gmt_modified INTEGER);`);
  raw.exec(flowTrigger);
  raw.exec(`ALTER TABLE flow_runs RENAME TO flow_runs_old; ALTER TABLE flow_runs_old RENAME TO flow_runs;
    INSERT INTO flow_runs VALUES (1, 'before', 0); UPDATE flow_runs SET flow_id = 'after' WHERE id = 1;`);
  expect((raw.prepare("SELECT sql FROM sqlite_master WHERE name='trg_flow_runs_update'").get() as { sql: string }).sql).toContain('"flow_runs"');
  expect((raw.prepare('SELECT gmt_modified FROM flow_runs').get() as { gmt_modified: number }).gmt_modified).toBeGreaterThan(0);
  const db = new SqliteDatabase(raw);
  try { await expect(runMigrations(db, 'sqlite')).resolves.toBeUndefined(); }
  finally { await db.close(); }
});

it.each(["'unixepoch()'", '"unixepoch()"'])('rejects a trigger using %s as a literal rather than the timestamp function', async literal => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER);
    CREATE TRIGGER trg_flow_runs_update AFTER UPDATE ON flow_runs FOR EACH ROW BEGIN
      UPDATE flow_runs SET gmt_modified = (${literal}) WHERE id = NEW.id; END;`);
  const db = new SqliteDatabase(raw);
  try { await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/trigger.*incompatible/i); }
  finally { await db.close(); }
});

it('does not cache a ready SQLite connection after a real deferred migration and retries on the next init', async () => {
  const { closeDatabase, getRepositories, initDatabase } = await import('../db.js');
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-deferred-retry-')); const path = join(dir, 'db.sqlite');
  const peer = new Database(path); peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT);
    INSERT INTO schema_version (version, description) VALUES (149, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES (1, 'existing', 0);`);
  for (const sql of sqliteTriggers.filter(ddl => /trg_(flow_runs|schema_version)_update\b/.test(ddl))) peer.exec(sql);
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  const original = SqliteDatabase.prototype.exec; let first = true; const failedConnections: Database.Database[] = [];
  const spy = vi.spyOn(SqliteDatabase.prototype, 'exec').mockImplementation(async function (this: SqliteDatabase, sql, params) {
    if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) {
      const connection = (this as unknown as { db: Database.Database }).db;
      connection.pragma('busy_timeout = 10');
      if (first) { first = false; peer.exec('BEGIN IMMEDIATE'); }
      if (peer.inTransaction) failedConnections.push(connection);
    }
    return original.call(this, sql, params);
  });
  try {
    await expect(initDatabase()).rejects.toThrow(/migrations deferred.*incomplete/i);
    expect(() => getRepositories()).toThrow(/not initialized/i);
    await expect(initDatabase()).rejects.toThrow(/migrations deferred.*incomplete/i);
    expect(failedConnections).toHaveLength(2);
    expect(failedConnections.every(connection => !connection.open)).toBe(true);
    expect(peer.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    peer.exec('ROLLBACK');
    const retried = await initDatabase();
    expect(retried.db.dbType).toBe('sqlite');
    expect(await retried.db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 151 }]);
    expect(await retried.db.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
  } finally {
    spy.mockRestore(); if (peer.inTransaction) peer.exec('ROLLBACK');
    await closeDatabase(); peer.close(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true });
  }
});
