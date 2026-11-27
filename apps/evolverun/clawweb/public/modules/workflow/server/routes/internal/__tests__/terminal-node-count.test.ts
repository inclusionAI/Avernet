// @vitest-environment node
import { afterAll, beforeAll, beforeEach, expect, it } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const raw = new Database(':memory:');
const db = new SqliteDatabase(raw);
const runs = new FlowRunRepository(db);
let server: Server;
let base: string;

beforeAll(async () => {
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, succeeded_count INTEGER,
    failed_count INTEGER, completed_at INTEGER, gmt_modified INTEGER,
    total_duration_ms INTEGER, total_token_usage INTEGER, current_phase TEXT,
    result_json TEXT, input_json TEXT
  )`);
  const app = express();
  app.use(express.json());
  app.use('/runs', createInternalRunsRouter(runs));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
beforeEach(() => {
  raw.exec("DELETE FROM flow_runs; INSERT INTO flow_runs (flow_id, status, succeeded_count, failed_count, completed_at, gmt_modified) VALUES ('flow', 'running', 2, 1, NULL, 456)");
});
afterAll(async () => {
  await new Promise<void>(resolve => server.close(() => resolve()));
  await db.close();
});

function row() { return raw.prepare('SELECT * FROM flow_runs').get(); }
function request(field: string, flowId = 'flow') {
  return fetch(`${base}/runs/${flowId}/increment-node`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ field }),
  });
}

for (const path of ['SQL', 'HTTP'] as const) {
  it.each(['succeeded', 'failed', 'cancelled', 'canceled', ' SUCCEEDED ', ' Failed ', ' CANCELLED ', ' Canceled ',
    '\tfailed\n', '\nSUCCEEDED\t', '\u00a0cancelled\ufeff', '\rcanceled\u000b', 'running', 'waiting', 'blocked', 'pending'])(
    `${path} rejects both increments on settled %j without changing any column`, async status => {
      raw.prepare("UPDATE flow_runs SET status = ?, completed_at = 123 WHERE flow_id = 'flow'").run(status);
      const before = row();
      for (const field of ['succeeded_count', 'failed_count'] as const) {
        if (path === 'SQL') {
          expect(await runs.incrementNodeCount('flow', field)).toBe(false);
        } else {
          const response = await request(field);
          // Preserve the existing increment route's false-write response; no route redesign.
          expect(response.status).toBe(404);
          expect(await response.json()).toEqual({ success: false, error: 'Not Found', message: 'Flow run "flow" not found' });
        }
        expect(row()).toEqual(before);
      }
    },
  );
}

it.each(['running', 'waiting', 'blocked', 'pending'])('keeps nonterminal %s HTTP increments working', async status => {
  raw.prepare("UPDATE flow_runs SET status = ? WHERE flow_id = 'flow'").run(status);
  for (const field of ['succeeded_count', 'failed_count']) {
    const response = await request(field);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
  }
  expect(row()).toMatchObject({ status, succeeded_count: 3, failed_count: 2, completed_at: null });
});

it('freezes completion-reconciled counts and allows a genuine reopened failed retry', async () => {
  expect(await runs.updateCompletion('flow', {
    status: 'failed', completedAt: 123, succeededCount: 2, failedCount: 1,
  })).toBe(true);
  const settled = row();
  expect((await request('failed_count')).status).toBe(404);
  expect(row()).toEqual(settled);
  expect(await runs.updateStatus('flow', 'running')).toBe(true);
  expect((await request('succeeded_count')).status).toBe(200);
  expect(row()).toMatchObject({ status: 'running', succeeded_count: 3, failed_count: 1, completed_at: null });
});

it('retains missing-row and invalid-field HTTP responses', async () => {
  expect(await runs.incrementNodeCount('missing', 'succeeded_count')).toBe(false);
  expect((await request('succeeded_count', 'missing')).status).toBe(404);
  const before = row();
  expect((await request('status')).status).toBe(400);
  expect(row()).toEqual(before);
});

it.each(['succeeded', 'failed', 'cancelled', 'canceled'])('freezes canonical terminal %s with no completion timestamp', async status => {
  raw.prepare("UPDATE flow_runs SET status = ? WHERE flow_id = 'flow'").run(status);
  const before = row();
  for (const field of ['succeeded_count', 'failed_count'] as const) {
    expect(await runs.incrementNodeCount('flow', field)).toBe(false);
    expect((await request(field)).status).toBe(404);
    expect(row()).toEqual(before);
  }
});
