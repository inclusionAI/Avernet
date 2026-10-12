// @vitest-environment node
import { afterAll, beforeAll, expect, it } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import { sqliteDialect } from '../../../../../../shared/server/db/dialect.js';
import type { Server } from 'node:http';
import { SqliteDatabase } from '../../../../../../shared/server/db.js';
import { recoveryClockMigration } from '../../../../../../shared/server/recovery-clock-schema.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { NodeExecutionRepository } from '../../../repositories/node-execution-repository.js';
import type { IDatabase } from '../../../../../../shared/server/db.js';
import { createInternalRunsRouter } from '../runs.js';

let server: Server;
let base: string;
const raw = new Database(':memory:');
const db = new SqliteDatabase(raw);
const claim = { mode: 'claim', ownerInstanceId: 'owner', bootId: 'boot', epoch: 0, seq: 0, elapsedMs: 0, phase: 'recovering' };
beforeAll(async () => {
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER, completed_at INTEGER,
    result_json TEXT, current_phase TEXT, total_duration_ms INTEGER);
    INSERT INTO flow_runs VALUES ('flow','running',123,NULL,NULL,NULL,NULL)`);
  for (const sql of recoveryClockMigration.sql) raw.exec(sqliteDialect.renderDdl(sql));
  const app = express();
  app.use(express.json());
  app.use('/api/internal/runs', createInternalRunsRouter(new FlowRunRepository(db)));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/internal/runs`;
});
afterAll(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await db.close(); });
function put(path: string, body: unknown) {
  return fetch(`${base}/${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
it('exposes the flat clock contract and rejects malformed, missing, and conflicting writes', async () => {
  expect(await (await fetch(`${base}/flow/recovery-clock`)).json()).toEqual({ clock: null });
  expect((await put('flow/recovery-clock', { ...claim, seq: '0' })).status).toBe(400);
  expect((await put('missing/recovery-clock', claim)).status).toBe(404);
  const result = await put('flow/recovery-clock', claim);
  expect(result.status).toBe(200);
  expect(await result.json()).toEqual({ ok: true, clock: { ownerInstanceId: 'owner', bootId: 'boot', epoch: 0, seq: 0, elapsedMs: 0, phase: 'recovering' } });
  expect((await put('flow/recovery-clock', { ...claim, ownerInstanceId: 'other' })).status).toBe(409);
  expect((await put('flow/retry-failed', {})).status).toBe(409);
  expect((await put('flow/started-at', { started_at: 999 })).status).toBe(409);
  expect(await (await put('flow/fail-if-running', { reason: 'timeout' })).json()).toEqual({ success: true, data: { claimed: false } });
  expect((await put('flow/fail-if-running', { reason: 'timeout', expectedClock: { epoch: 0 } })).status).toBe(400);
  const active = { ...claim, mode: 'checkpoint', seq: 1, phase: 'active' };
  expect((await put('flow/recovery-clock', active)).status).toBe(200);
  expect(await (await put('flow/fail-if-running', { reason: 'timeout', expectedClock: active })).json()).toEqual({ success: true, data: { claimed: true } });
});
it('returns storage errors instead of pretending an unavailable clock is absent', async () => {
  raw.exec('DROP TABLE flow_recovery_clocks');
  expect((await fetch(`${base}/flow/recovery-clock`)).status).toBe(500);
  expect((await put('flow/recovery-clock', claim)).status).toBe(500);
  expect((await put('flow/fail-if-running', { reason: 'timeout' })).status).toBe(500);
});

it('does not leave child nodes reset when the first clock claim races legacy retry', async () => {
  const localRaw = new Database(':memory:');
  localRaw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  localRaw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, failed_count INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('racing-flow', 'failed', 1, 999, 999);
    CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT, duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO node_executions VALUES ('racing-flow', 'failed', 'error', 10, 999, 999)`);
  for (const sql of recoveryClockMigration.sql) localRaw.exec(sqliteDialect.renderDdl(sql));
  const localDb = new SqliteDatabase(localRaw);
  const runs = new FlowRunRepository(localDb);
  runs.findByFlowId = async () => ({ flow_id: 'racing-flow' }) as Awaited<ReturnType<FlowRunRepository['findByFlowId']>>;
  let claimPromise: Promise<unknown> | undefined;
  class RacingNodes extends NodeExecutionRepository {
    override async resetFailedByFlowId(flowId: string, tx?: IDatabase): Promise<number> {
      if (tx) claimPromise = runs.putRecoveryClock(flowId, { ...claim, bootId: 'racing-boot' });
      else await runs.putRecoveryClock(flowId, { ...claim, bootId: 'racing-boot' });
      const reset = super.resetFailedByFlowId as (flowId: string, tx?: IDatabase) => Promise<number>;
      return reset.call(this, flowId, tx);
    }
  }
  const localApp = express();
  localApp.use(express.json());
  localApp.use('/api/internal/runs', createInternalRunsRouter(runs, new RacingNodes(localDb)));
  const localServer = await new Promise<Server>(resolve => {
    const listening = localApp.listen(0, '127.0.0.1', () => resolve(listening));
  });
  try {
    const url = `http://127.0.0.1:${(localServer.address() as { port: number }).port}/api/internal/runs/racing-flow/retry-failed`;
    const response = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    expect(claimPromise).toBeDefined();
    await claimPromise;
    expect(response.status).toBe(200);
    expect(await runs.getRecoveryClock('racing-flow')).toEqual(expect.objectContaining({ bootId: 'racing-boot' }));
    expect(localRaw.prepare('SELECT status, failed_count FROM flow_runs').get()).toEqual({ status: 'running', failed_count: 0 });
    expect(localRaw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'pending' });
  } finally {
    await new Promise<void>(resolve => localServer.close(() => resolve()));
    await localDb.close();
  }
});
