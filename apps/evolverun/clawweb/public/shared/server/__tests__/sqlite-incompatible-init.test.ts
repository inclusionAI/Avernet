import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { expect, it, vi } from 'vitest';
import { closeDatabase, getRepositories, initDatabase, SqliteDatabase } from '../db.js';

it.each([150, 151] as const)('rejects real SQLite v%s incompatible schema and retries after repair without closing', async version => {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-incompatible-init-'));
  const path = join(dir, 'test.sqlite');
  const peer = new Database(path); peer.pragma('journal_mode = WAL');
  peer.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER, description TEXT, gmt_modified INTEGER);
    INSERT INTO schema_version (version, description) VALUES (${version - 1}, 'old');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, gmt_modified INTEGER${version === 151 ? ', ordinary_run_marker INTEGER' : ''});
    INSERT INTO flow_runs (id, flow_id, gmt_modified) VALUES (1, 'existing', 0);
    CREATE TABLE ordinary_run_requests (request_key_hash TEXT NOT NULL PRIMARY KEY, parent_flow_id TEXT NOT NULL,
      child_flow_id TEXT NOT NULL UNIQUE, ${version === 150 ? 'wrong_name' : 'child_workflow_id'} TEXT NOT NULL,
      request_fingerprint TEXT NOT NULL, parent_run_marker TEXT, child_run_marker TEXT);
    INSERT INTO ordinary_run_requests (request_key_hash, parent_flow_id, child_flow_id, ${version === 150 ? 'wrong_name' : 'child_workflow_id'}, request_fingerprint)
      VALUES ('key', 'parent', 'child', 'workflow', 'fingerprint');`);
  vi.stubEnv('DATABASE_MODE', 'sqlite'); vi.stubEnv('SQLITE_PATH', path);
  const closed: Database.Database[] = [];
  const original = SqliteDatabase.prototype.close;
  const spy = vi.spyOn(SqliteDatabase.prototype, 'close').mockImplementation(async function (this: SqliteDatabase) {
    const raw = (this as unknown as { db: Database.Database }).db;
    await original.call(this); closed.push(raw);
  });
  try {
    await expect(initDatabase()).rejects.toThrow(/migrations deferred.*incomplete/i);
    expect(() => getRepositories()).toThrow(/not initialized/i);
    expect(closed).toHaveLength(1); expect(closed[0].open).toBe(false);
    expect(peer.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: version - 1 });
    expect(peer.prepare('SELECT flow_id FROM flow_runs').all()).toEqual([{ flow_id: 'existing' }]);
    if (version === 150) peer.exec('ALTER TABLE ordinary_run_requests RENAME COLUMN wrong_name TO child_workflow_id');
    else peer.exec('ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker; ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker TEXT');
    const { db } = await initDatabase();
    expect(db.dbType).toBe('sqlite');
    expect(await db.query('SELECT MAX(version) AS v FROM schema_version')).toEqual([{ v: 151 }]);
    expect(await db.query('SELECT request_key_hash, child_workflow_id FROM ordinary_run_requests')).toEqual([{ request_key_hash: 'key', child_workflow_id: 'workflow' }]);
    expect(await db.query('SELECT flow_id FROM flow_runs')).toEqual([{ flow_id: 'existing' }]);
  } finally {
    spy.mockRestore(); await closeDatabase(); peer.close(); vi.unstubAllEnvs(); rmSync(dir, { recursive: true, force: true });
  }
});
