import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { ordinaryRunRequestDdl } from '@avernet/clawweb-shared/server/schema-ordinary-run-request';
import { assertOrdinaryRequestSchema } from '../ordinary-run-request-repository.js';

it('leaves old SQLite storage usable but ordinary requests unavailable when ALTER is denied', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (150,'old');
    CREATE TABLE flow_runs (flow_id TEXT NOT NULL PRIMARY KEY); INSERT INTO flow_runs VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL);`);
  const real = new SqliteDatabase(raw);
  const denied = Object.assign(new Error('not authorized'), { code: 'SQLITE_AUTH' });
  const db: IDatabase = {
    dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real),
    transaction: real.transaction.bind(real), close: real.close.bind(real),
    exec: async (sql, params) => {
      if (sql.startsWith('ALTER TABLE flow_runs ADD COLUMN')) throw denied;
      return real.exec(sql, params);
    },
  };
  try {
    await runMigrations(db, 'sqlite');
    expect(await real.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
    await expect(assertOrdinaryRequestSchema(real)).rejects.toThrow('Invalid ordinary request marker columns');
    expect(raw.prepare('SELECT count(*) AS n FROM schema_version WHERE version=151').get()).toEqual({ n: 0 });
    await runMigrations(real, 'sqlite');
    expect(raw.prepare('SELECT count(*) AS n FROM schema_version WHERE version=151').get()).toEqual({ n: 1 });
    await expect(assertOrdinaryRequestSchema(real)).resolves.toBeUndefined();
  } finally { await real.close(); }
});

it('defers a SQLite v150 CREATE authorization denial without recording progress and resumes after provisioning', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (149,'old');
    CREATE TABLE flow_runs (flow_id TEXT NOT NULL PRIMARY KEY); INSERT INTO flow_runs VALUES ('existing');`);
  const real = new SqliteDatabase(raw);
  const db: IDatabase = { dbType: 'sqlite', dialect: real.dialect, query: real.query.bind(real),
    transaction: real.transaction.bind(real), close: real.close.bind(real),
    exec: async (sql, params) => {
      if (sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')) throw Object.assign(new Error('not authorized'), { code: 'SQLITE_AUTH' });
      return real.exec(sql, params);
    },
  };
  try {
    await runMigrations(db, 'sqlite');
    expect(await real.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
    await expect(assertOrdinaryRequestSchema(real)).rejects.toThrow();
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 149 });
    await real.exec(real.dialect.renderDdl(ordinaryRunRequestDdl));
    await runMigrations(real, 'sqlite');
    await expect(assertOrdinaryRequestSchema(real)).resolves.toBeUndefined();
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
  } finally { await real.close(); }
});

it.each([
  ['NOT NULL DEFAULT \'x\'', 'not nullable'],
  ["DEFAULT 'x'", 'non-null default'],
])('defers incompatible existing SQLite marker (%s) without changing old data, and retries after repair', async (definition) => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, gmt_modified INTEGER, version INTEGER, description TEXT); INSERT INTO schema_version (version,description) VALUES (150,'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, gmt_modified INTEGER, flow_id TEXT NOT NULL UNIQUE, ordinary_run_marker VARCHAR(64) ${definition});
    INSERT INTO flow_runs (flow_id) VALUES ('existing');
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, child_workflow_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
      parent_run_marker VARCHAR(64), child_run_marker VARCHAR(64));`);
  const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 150 });
    expect(raw.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    await expect(assertOrdinaryRequestSchema(db)).rejects.toThrow('Invalid ordinary request marker columns');
    raw.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker; ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker VARCHAR(64) DEFAULT NULL');
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
    await expect(assertOrdinaryRequestSchema(db)).resolves.toBeUndefined();
  } finally { await db.close(); }
});
