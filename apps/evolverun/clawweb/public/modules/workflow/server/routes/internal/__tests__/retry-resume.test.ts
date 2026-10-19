// @vitest-environment node
import { afterAll, beforeAll, expect, it } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const raw = new Database(':memory:');
const db = new SqliteDatabase(raw);
let server: Server;
let base: string;
beforeAll(async () => {
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER,
    retry_epoch INTEGER DEFAULT NULL,
    completed_at INTEGER, result_json TEXT, current_phase TEXT, total_duration_ms INTEGER, gmt_modified INTEGER,
    total_token_usage INTEGER, succeeded_count INTEGER, failed_count INTEGER, input_json TEXT);
    INSERT INTO flow_runs (flow_id,status,started_at,completed_at,result_json,gmt_modified)
    VALUES ('flow','failed',123,456,'old result',1);
    CREATE TABLE node_executions (flow_id TEXT, node_id TEXT, status TEXT);
    INSERT INTO node_executions VALUES ('flow','node','failed')`);
  const app = express();
  app.use(express.json());
  app.use('/api/internal/runs', createInternalRunsRouter(new FlowRunRepository(db)));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/internal/runs`;
});
afterAll(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await db.close(); });

function put(path: string, body: unknown = {}) {
  return fetch(`${base}/${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

it('resumes a retry idempotently, preserving original start and node history', async () => {
  expect((await put('missing/retry-resume', { retry_epoch: 1 })).status).toBe(404);
  expect((await put('flow/retry-resume', { retry_epoch: 1 })).status).toBe(200);
  expect((await put('flow/retry-resume', { retry_epoch: 1 })).status).toBe(200);
  expect(raw.prepare('SELECT status, started_at, completed_at, retry_epoch FROM flow_runs WHERE flow_id = ?').get('flow'))
    .toEqual({ status: 'running', started_at: 123, completed_at: null, retry_epoch: 1 });
  expect(raw.prepare('SELECT status FROM node_executions WHERE flow_id = ?').get('flow')).toEqual({ status: 'failed' });
  expect(raw.prepare("SELECT name FROM sqlite_master WHERE name LIKE 'flow_recovery_%'").all()).toEqual([]);
});

it('the timeout claim still wins only once', async () => {
  const stale = await (await put('flow/fail-if-running', { reason: 'old timeout', retry_epoch: 0 })).json();
  const first = await (await put('flow/fail-if-running', { reason: 'timeout', retry_epoch: 1 })).json();
  const second = await (await put('flow/fail-if-running', { reason: 'timeout', retry_epoch: 1 })).json();
  expect(stale).toEqual({ success: true, data: { claimed: false } });
  expect(first).toEqual({ success: true, data: { claimed: true } });
  expect(second).toEqual({ success: true, data: { claimed: false } });
  expect((await put('flow/completion', { status: 'succeeded', retry_epoch: 1 })).status).toBe(409);
  expect(raw.prepare('SELECT status, result_json FROM flow_runs WHERE flow_id = ?').get('flow'))
    .toEqual({ status: 'failed', result_json: 'timeout' });
});

it('keeps the legacy status endpoint able to resume a failed TaskGuard run', async () => {
  raw.prepare('INSERT INTO flow_runs (flow_id, status, started_at) VALUES (?, ?, ?)').run('legacy-status', 'failed', 123);
  expect((await put('legacy-status/status', { status: 'running' })).status).toBe(200);
  expect(raw.prepare('SELECT status, started_at FROM flow_runs WHERE flow_id = ?').get('legacy-status'))
    .toEqual({ status: 'running', started_at: 123 });
});

it('keeps retry available for waiting, blocked, and previously supported terminal runs', async () => {
  for (const status of ['waiting', 'blocked', 'succeeded', 'cancelled']) {
    raw.prepare('INSERT INTO flow_runs (flow_id, status, started_at, completed_at) VALUES (?, ?, 123, 456)')
      .run(status, status);
    const response = await put(`${status}/retry-resume`, { retry_epoch: 1 });
    expect(response.status).toBe(200);
    expect(raw.prepare('SELECT status, started_at FROM flow_runs WHERE flow_id = ?').get(status))
      .toEqual({ status: 'running', started_at: 123 });
  }
});

it('keeps the legacy status endpoint able to explicitly restart a completed run', async () => {
  raw.prepare('INSERT INTO flow_runs (flow_id, status, started_at) VALUES (?, ?, ?)')
    .run('legacy-order', 'failed', 123);
  expect((await put('legacy-order/completion', { status: 'succeeded', completed_at: 789 })).status).toBe(200);
  expect((await put('legacy-order/status', { status: 'running' })).status).toBe(200);
  expect(raw.prepare('SELECT status, completed_at FROM flow_runs WHERE flow_id = ?').get('legacy-order'))
    .toEqual({ status: 'running', completed_at: null });
});

it('rejects a stale completion without changing the newer retry', async () => {
  raw.prepare('INSERT INTO flow_runs (flow_id, status, started_at) VALUES (?, ?, ?)').run('late', 'failed', 123);
  expect((await put('late/retry-resume', { retry_epoch: 1 })).status).toBe(200);
  expect((await put('late/completion', { status: 'succeeded', retry_epoch: 0 })).status).toBe(409);
  expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs WHERE flow_id = ?').get('late'))
    .toEqual({ status: 'running', retry_epoch: 1 });
  expect((await put('late/completion', { status: 'succeeded', retry_epoch: 1 })).status).toBe(200);
});

it('supports legacy status writes while retaining explicit epoch compatibility', async () => {
  raw.prepare('INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES (?, ?, ?)')
    .run('status-epoch', 'running', 1);
  expect((await put('status-epoch/status', { status: 'waiting', retry_epoch: 0 })).status).not.toBe(200);
  expect((await put('status-epoch/status', { status: 'waiting' })).status).toBe(200);
  expect((await put('status-epoch/status', { status: 'waiting', retry_epoch: 1 })).status).toBe(200);
  expect(raw.prepare('SELECT status FROM flow_runs WHERE flow_id = ?').get('status-epoch'))
    .toEqual({ status: 'waiting' });
});

it('accepts original completion and watchdog requests for previously epoch-enabled records', async () => {
  raw.prepare('INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES (?, ?, ?)').run('compatible-completion', 'running', 2);
  expect((await put('compatible-completion/completion', { status: 'succeeded' })).status).toBe(200);
  raw.prepare('INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES (?, ?, ?)').run('compatible-timeout', 'running', 2);
  expect(await (await put('compatible-timeout/fail-if-running', { reason: 'timeout' })).json())
    .toEqual({ success: true, data: { claimed: true } });
});
