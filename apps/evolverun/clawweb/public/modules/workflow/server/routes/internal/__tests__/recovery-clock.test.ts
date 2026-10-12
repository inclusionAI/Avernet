// @vitest-environment node
import { afterAll, beforeAll, expect, it } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import { sqliteDialect } from '../../../../../../shared/server/db/dialect.js';
import type { Server } from 'node:http';
import { SqliteDatabase } from '../../../../../../shared/server/db.js';
import { recoveryClockMigration } from '../../../../../../shared/server/recovery-clock-schema.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
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
