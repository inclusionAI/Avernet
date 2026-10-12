// @vitest-environment node
import { afterAll, beforeAll, expect, it } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { SqliteDatabase } from '../../../../../../shared/server/db.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const raw = new Database(':memory:');
const db = new SqliteDatabase(raw);
let server: Server;
let base: string;
beforeAll(async () => {
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER,
    completed_at INTEGER, result_json TEXT, current_phase TEXT, total_duration_ms INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('flow','failed',123,456,'old result',NULL,NULL,1);
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

it('resumes a retry idempotently using existing tables, preserving original start and node history', async () => {
  expect((await put('missing/retry-resume')).status).toBe(404);
  expect((await put('flow/retry-resume')).status).toBe(200);
  expect((await put('flow/retry-resume')).status).toBe(200);
  expect(raw.prepare('SELECT status, started_at, completed_at FROM flow_runs WHERE flow_id = ?').get('flow'))
    .toEqual({ status: 'running', started_at: 123, completed_at: null });
  expect(raw.prepare('SELECT status FROM node_executions WHERE flow_id = ?').get('flow')).toEqual({ status: 'failed' });
  expect(raw.prepare("SELECT name FROM sqlite_master WHERE name LIKE 'flow_recovery_%'").all()).toEqual([]);
});

it('the timeout claim still wins only once', async () => {
  const first = await (await put('flow/fail-if-running', { reason: 'timeout' })).json();
  const second = await (await put('flow/fail-if-running', { reason: 'timeout' })).json();
  expect(first).toEqual({ success: true, data: { claimed: true } });
  expect(second).toEqual({ success: true, data: { claimed: false } });
});

it('keeps the legacy status endpoint able to resume a failed TaskGuard run', async () => {
  expect((await put('flow/status', { status: 'running' })).status).toBe(200);
  expect(raw.prepare('SELECT status, started_at FROM flow_runs WHERE flow_id = ?').get('flow'))
    .toEqual({ status: 'running', started_at: 123 });
});
