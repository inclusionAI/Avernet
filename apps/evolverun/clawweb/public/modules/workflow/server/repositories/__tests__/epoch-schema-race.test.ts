import { afterEach, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); });
function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-epoch-schema-'));
  const path = join(dir, 'runs.sqlite'); const raw = new Database(path); const peer = new Database(path);
  peer.pragma('busy_timeout = 0');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER,
    state_json TEXT, completed_at INTEGER, result_json TEXT, input_json TEXT, current_phase TEXT,
    total_duration_ms INTEGER, total_token_usage INTEGER, succeeded_count INTEGER, failed_count INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('flow', 'running', 1, '{}', NULL, 'before', NULL, 'P1', NULL, NULL, 0, 0, 0);
    CREATE TABLE node_executions (flow_id TEXT, status TEXT);`);
  const db = new SqliteDatabase(raw); const peerDb = new SqliteDatabase(peer);
  cleanups.push(async () => { await db.close(); await peerDb.close(); rmSync(dir, { recursive: true, force: true }); });
  const upgradeAndRetry = async () => {
    peer.exec('ALTER TABLE flow_runs ADD COLUMN retry_epoch INTEGER DEFAULT 0');
    expect(await new FlowRunRepository(peerDb).resumeForRetry('flow', 1)).toBe('resumed');
  };
  return { db, raw, upgradeAndRetry };
}
const writes = {
  completion: (repo: FlowRunRepository) => repo.updateCompletion('flow', { status: 'failed', resultJson: 'old failure', completedAt: 10, succeededCount: 0, failedCount: 0 }),
  watchdog: (repo: FlowRunRepository) => repo.markFailedIfRunning('flow', { reason: 'old timeout', completedAt: 10 }),
  status: (repo: FlowRunRepository) => repo.updateStatus('flow', 'waiting'),
};
for (const [name, write] of Object.entries(writes)) {
  it(`${name}: live schema upgrade cannot remove epoch fencing after fallback SELECT`, async () => {
    const f = fixture(); let attempted = false; let blocked = false;
    const wrap = (db: IDatabase): IDatabase => ({ dbType: db.dbType, dialect: db.dialect,
      query: async <T>(sql: string, params?: unknown[]) => {
        const rows = await db.query<T>(sql, params);
        if (!attempted && /^\s*SELECT\b/i.test(sql) && /FROM flow_runs/i.test(sql) && !/retry_epoch/i.test(sql)) {
          attempted = true;
          try { await f.upgradeAndRetry(); }
          catch (error) { if ((error as { code?: string }).code !== 'SQLITE_BUSY') throw error; blocked = true; }
        }
        return rows;
      }, exec: (sql, params) => db.exec(sql, params), transaction: fn => db.transaction(tx => fn(wrap(tx))), close: async () => {},
    });
    expect(await write(new FlowRunRepository(wrap(f.db)))).toBe(true);
    expect(attempted).toBe(true); expect(blocked).toBe(true);
    // The lock is released after the old epoch-zero write, so a later migration
    // and real retry can proceed; the same old writer is now rejected.
    await f.upgradeAndRetry();
    expect(await write(new FlowRunRepository(f.db))).toBe(false);
    expect(f.raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'running', retry_epoch: 1 });
  });
  it(`${name}: schema is re-probed under the fallback transaction, never assumed absent`, async () => {
    const f = fixture(); let upgraded = false;
    const wrapped: IDatabase = { dbType: f.db.dbType, dialect: f.db.dialect,
      query: async <T>(sql: string, params?: unknown[]) => {
        try { return await f.db.query<T>(sql, params); }
        catch (error) {
          if (!upgraded && /no such column: retry_epoch/.test(String(error))) { upgraded = true; await f.upgradeAndRetry(); }
          throw error;
        }
      }, exec: (sql, params) => f.db.exec(sql, params), transaction: fn => f.db.transaction(fn), close: async () => {},
    };
    expect(await write(new FlowRunRepository(wrapped))).toBe(false);
    expect(upgraded).toBe(true);
    expect(f.raw.prepare('SELECT status, retry_epoch, result_json FROM flow_runs').get()).toEqual({ status: 'running', retry_epoch: 1, result_json: 'before' });
  });
}
