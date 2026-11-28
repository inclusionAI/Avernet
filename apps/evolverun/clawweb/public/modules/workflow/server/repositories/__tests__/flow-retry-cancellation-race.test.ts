// @vitest-environment node
import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRetryRequestRepository } from '../flow-retry-request-repository.js';

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-cancellation-'));
  const path = join(dir, 'runs.sqlite');
  const raw = new Database(path);
  raw.pragma('journal_mode = WAL');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, completed_at INTEGER, result_json TEXT, gmt_modified INTEGER
  );
  INSERT INTO flow_runs (flow_id, status) VALUES ('flow', 'failed');
  CREATE TABLE flow_retry_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE, flow_id TEXT, node_id TEXT, reason TEXT, requester TEXT,
    options_json TEXT, status TEXT, claim_engine TEXT, claimed_at INTEGER, attempts INTEGER, result_message TEXT,
    gmt_create INTEGER, gmt_modified INTEGER
  );`);
  const other = new Database(path);
  const db = new SqliteDatabase(raw);
  const cancellationDb = new SqliteDatabase(other);
  return {
    raw, db, cancellationDb,
    close: async () => { await db.close(); await cancellationDb.close(); rmSync(dir, { recursive: true, force: true }); },
  };
}

for (const status of ['cancelled', 'canceled']) {
  for (const state of ['pending', 'claimed']) {
    it(`rejects a ${state} CAS after ${status} commits on a separate connection`, async () => {
      const { raw, db, cancellationDb, close } = fixture();
      try {
        const retries = new FlowRetryRequestRepository(db);
        await retries.create({ requestId: 'request', flowId: 'flow' });
        if (state === 'claimed') await retries.claimPending(1, 60, 'old-engine', 100);
        const before = raw.prepare('SELECT * FROM flow_retry_requests').get();
        let selected = 0;
        const racing: IDatabase = {
          dbType: db.dbType, dialect: db.dialect,
          exec: db.exec.bind(db), transaction: db.transaction.bind(db), close: async () => {},
          query: async <T>(sql: string, params?: unknown[]) => {
            const rows = await db.query<T>(sql, params);
            if (/SELECT \* FROM flow_retry_requests/i.test(sql)) {
              expect(rows).toHaveLength(1);
              selected += 1;
              // A different connection commits cancellation after the real
              // candidate SELECT and before the real CAS statement executes.
              // This fixture already has a terminal failed parent. Public abort
              // now correctly refuses it; inject the cancellation precondition
              // directly to keep testing retry-request CAS, not abort admission.
              await cancellationDb.exec('UPDATE flow_runs SET status = ? WHERE flow_id = ?', [status, 'flow']);
            }
            return rows;
          },
        };
        expect(await new FlowRetryRequestRepository(racing).claimPending(1, 60, 'new-engine', 1000)).toEqual([]);
        expect(selected).toBe(1);
        expect(raw.prepare('SELECT status FROM flow_runs').get()).toEqual({ status });
        expect(raw.prepare('SELECT * FROM flow_retry_requests').get()).toEqual(before);
      } finally { await close(); }
    });
  }
}

it('concurrent failed-flow creators share one pending request on the real transaction adapter', async () => {
  const { raw, db, close } = fixture();
  try {
    const retries = new FlowRetryRequestRepository(db);
    const results = await Promise.all([
      retries.create({ requestId: 'first', flowId: 'flow' }),
      retries.create({ requestId: 'second', flowId: 'flow' }),
    ]);
    expect(results.map(row => row?.request_id)).toEqual(['first', 'first']);
    expect(raw.prepare('SELECT COUNT(*) AS count FROM flow_retry_requests').get()).toEqual({ count: 1 });
  } finally { await close(); }
});
