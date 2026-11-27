// @vitest-environment node
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { NodeExecutionRepository } from '../../../repositories/node-execution-repository.js';
import { FlowRetryRequestRepository } from '../../../repositories/flow-retry-request-repository.js';
import { createInternalRunsRouter } from '../runs.js';
import { createInternalRetryRequestsRouter } from '../retry-requests.js';

const raw = new Database(':memory:');
const db = new SqliteDatabase(raw);
const runs = new FlowRunRepository(db);
const retries = new FlowRetryRequestRepository(db);
let server: Server;
let base: string;

beforeAll(async () => {
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, retry_epoch INTEGER, started_at INTEGER, completed_at INTEGER,
    state_json TEXT DEFAULT '{"effectiveFlowTimeoutMinutes":1}',
    result_json TEXT, current_phase TEXT, total_duration_ms INTEGER, total_token_usage INTEGER, gmt_modified INTEGER,
    succeeded_count INTEGER, failed_count INTEGER, input_json TEXT
  );
  CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT, duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
  CREATE TABLE flow_retry_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE, flow_id TEXT, node_id TEXT, reason TEXT, requester TEXT,
    options_json TEXT, status TEXT, claim_engine TEXT, claimed_at INTEGER, attempts INTEGER, result_message TEXT,
    gmt_create INTEGER, gmt_modified INTEGER
  );`);
  const app = express();
  app.use(express.json());
  app.use('/runs', createInternalRunsRouter(runs, new NodeExecutionRepository(db)));
  app.use('/retry-requests', createInternalRetryRequestsRouter(retries));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
beforeEach(() => {
  raw.exec('DELETE FROM flow_retry_requests; DELETE FROM node_executions; DELETE FROM flow_runs;');
  raw.prepare("INSERT INTO flow_runs (flow_id, status, retry_epoch, started_at, completed_at, result_json, failed_count) VALUES ('flow', 'failed', 0, 123, 999, 'original result', 1)").run();
  raw.prepare("INSERT INTO node_executions VALUES ('flow', 'failed', 'original error', 10, 999, 999)").run();
});
afterAll(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await db.close(); });

function request(path: string, body: unknown, method = 'PUT') {
  return fetch(`${base}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
function cancel(status: string) { raw.prepare('UPDATE flow_runs SET status = ?, result_json = ? WHERE flow_id = ?').run(status, 'user aborted', 'flow'); }
function runRow() { return raw.prepare('SELECT * FROM flow_runs').get(); }

for (const status of ['cancelled', 'canceled']) {
  describe(status, () => {
    it('returns honest conflicts for no-epoch/current-epoch completion, status and both retry APIs', async () => {
      cancel(status);
      const before = runRow();
      for (const epoch of [undefined, 0]) {
        const completion = await request('/runs/flow/completion', { status: 'failed', retry_epoch: epoch, result_json: 'late error' });
        expect(completion.status).toBe(409);
        expect((await completion.json()).success).toBe(false);
        expect((await request('/runs/flow/status', { status: 'running', retry_epoch: epoch })).status).toBe(409);
        expect((await request('/runs/flow/status', { status: 'failed', retry_epoch: epoch })).status).toBe(409);
      }
      expect((await request('/runs/flow/retry-failed', {})).status).toBe(409);
      expect((await request('/runs/flow/retry-resume', { retry_epoch: 1 })).status).toBe(409);
      const timeout = await request('/runs/flow/fail-if-running', { reason: JSON.stringify({ reason: 'flow_timeout', timeoutSecs: 60, ranForSecs: 61 }) });
      expect(timeout.status).toBe(200);
      expect(await timeout.json()).toEqual({ success: true, data: { claimed: false } });
      expect(runRow()).toEqual(before);
      expect(raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'failed' });
    });

    it.each(['none', 'pending', 'claimed'])('POST rejects cancelled flows even with an existing %s request', async (open) => {
      if (open !== 'none') {
        await retries.create({ requestId: 'old', flowId: 'flow' });
        if (open === 'claimed') await retries.claimPending(1, 60, 'old-engine', 100);
      }
      cancel(status);
      const response = await request('/retry-requests', { request_id: 'new', flow_id: 'flow' }, 'POST');
      expect(response.status).toBe(409);
      expect((await response.json()).success).toBe(false);
      expect(raw.prepare("SELECT COUNT(*) AS count FROM flow_retry_requests WHERE request_id = 'new'").get()).toEqual({ count: 0 });
    });

    it.each(['pending', 'claimed', 'released'])('never claims %s mailbox entries after cancellation', async (state) => {
      await retries.create({ requestId: 'old', flowId: 'flow' });
      if (state !== 'pending') await retries.claimPending(1, 60, 'old-engine', 100);
      if (state === 'released') await retries.release('old');
      cancel(status);
      const before = raw.prepare('SELECT * FROM flow_retry_requests').get();
      const response = await request('/retry-requests/claim', { claim_engine: 'new-engine', lease_secs: 60, options_version: 1 }, 'POST');
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ success: true, data: { claimed: [] } });
      expect(raw.prepare('SELECT * FROM flow_retry_requests').get()).toEqual(before);
    });

    it('does not return a deduped open request if cancellation lands before that read', async () => {
      await retries.create({ requestId: 'existing', flowId: 'flow' });
      const racing = interceptWrites(() => {}, sql => {
        if (/SELECT[\s\S]*FROM flow_retry_requests/i.test(sql)) cancel(status);
      });
      await expect(new FlowRetryRequestRepository(racing).create({ requestId: 'duplicate', flowId: 'flow' })).rejects.toThrow(/cancel/i);
      expect(raw.prepare('SELECT request_id FROM flow_retry_requests').all()).toEqual([{ request_id: 'existing' }]);
    });

    it('guards the INSERT itself if cancellation lands after the initial reads', async () => {
      const racing = interceptWrites(sql => { if (/INSERT INTO flow_retry_requests/i.test(sql)) cancel(status); });
      await expect(new FlowRetryRequestRepository(racing).create({ requestId: 'racing', flowId: 'flow' })).rejects.toThrow(/cancel/i);
      expect(raw.prepare('SELECT * FROM flow_retry_requests').all()).toEqual([]);
    });

    it.each(['pending', 'claimed'])('guards the %s claim CAS when cancellation lands after candidate selection', async (state) => {
      await retries.create({ requestId: 'race-claim', flowId: 'flow' });
      if (state === 'claimed') await retries.claimPending(1, 60, 'old-engine', 100);
      const before = raw.prepare('SELECT * FROM flow_retry_requests').get();
      const racing = interceptWrites(sql => { if (/UPDATE flow_retry_requests SET status = 'claimed'/i.test(sql)) cancel(status); });
      expect(await new FlowRetryRequestRepository(racing).claimPending(1, 60, 'new-engine', 1000)).toEqual([]);
      expect(raw.prepare('SELECT * FROM flow_retry_requests').get()).toEqual(before);
    });
  });
}

// Inject at the write boundary, not by returning fake SQL results: the real
// SQLite INSERT/CAS must observe cancellation after candidate/preflight reads.
function interceptWrites(before: (sql: string) => void, beforeRead: (sql: string) => void = () => {}): IDatabase {
  function wrap(connection: IDatabase): IDatabase {
    return {
      dbType: connection.dbType, dialect: connection.dialect,
      query: async <T>(sql: string, params?: unknown[]) => { beforeRead(sql); return connection.query<T>(sql, params); }, close: async () => {},
      exec: async (sql, params) => { before(sql); return connection.exec(sql, params); },
      transaction: fn => connection.transaction(tx => fn(wrap(tx))),
    };
  }
  return wrap(db);
}

it('preserves failed mailbox dedupe/claim, legacy status, failed-node retry and retry-resume', async () => {
  const first = await request('/retry-requests', { request_id: 'first', flow_id: 'flow' }, 'POST');
  expect(first.status).toBe(200);
  const duplicate = await request('/retry-requests', { request_id: 'duplicate', flow_id: 'flow' }, 'POST');
  expect((await duplicate.json()).data.request_id).toBe('first');
  const claim = await request('/retry-requests/claim', { claim_engine: 'engine', options_version: 1 }, 'POST');
  expect((await claim.json()).data.claimed).toHaveLength(1);
  expect((await request('/runs/flow/status', { status: 'running' })).status).toBe(200);
  expect((await request('/runs/flow/completion', { status: 'failed' })).status).toBe(200);
  expect((await request('/runs/flow/retry-failed', {})).status).toBe(200);
  expect(raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'pending' });
  expect((await request('/runs/flow/retry-resume', { retry_epoch: 1 })).status).toBe(200);
});

it('still reports missing runs as 404, not cancellation conflicts', async () => {
  expect((await request('/runs/missing/status', { status: 'running' })).status).toBe(404);
  expect((await request('/runs/missing/retry-failed', {})).status).toBe(404);
  expect((await request('/runs/missing/retry-resume', { retry_epoch: 1 })).status).toBe(404);
});


it('cancelled entries do not consume the claim limit ahead of an ordinary failed flow', async () => {
  await retries.create({ requestId: 'cancelled-first', flowId: 'flow' });
  cancel('cancelled');
  raw.prepare("INSERT INTO flow_runs (flow_id, status) VALUES ('healthy-failure', 'failed')").run();
  await retries.create({ requestId: 'failed-second', flowId: 'healthy-failure' });
  const claimed = await retries.claimPending(1, 60, 'engine', 1000);
  expect(claimed.map(row => row.request_id)).toEqual(['failed-second']);
});

it('keeps genuine mailbox database failures as HTTP 500, not cancellation conflicts', async () => {
  raw.exec("CREATE TRIGGER reject_retry_insert BEFORE INSERT ON flow_retry_requests BEGIN SELECT RAISE(ABORT, 'write rejected'); END;");
  try {
    expect((await request('/retry-requests', { request_id: 'db-failure', flow_id: 'flow' }, 'POST')).status).toBe(500);
  } finally { raw.exec('DROP TRIGGER reject_retry_insert;'); }
});

it('does not misreport a failed cancellation lookup as a missing run', async () => {
  cancel('cancelled');
  const before = runRow();
  const failing = interceptWrites(() => {}, () => { throw new Error('status lookup unavailable'); });
  const app = express();
  app.use(express.json());
  app.use('/runs', createInternalRunsRouter(new FlowRunRepository(failing)));
  let local: Server;
  await new Promise<void>(resolve => { local = app.listen(0, '127.0.0.1', resolve); });
  try {
    const response = await fetch(`http://127.0.0.1:${(local!.address() as { port: number }).port}/runs/flow/status`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'running' }),
    });
    expect(response.status).toBe(500);
    expect((await response.json()).success).toBe(false);
    expect(runRow()).toEqual(before);
  } finally { await new Promise<void>(resolve => local!.close(() => resolve())); }
});
