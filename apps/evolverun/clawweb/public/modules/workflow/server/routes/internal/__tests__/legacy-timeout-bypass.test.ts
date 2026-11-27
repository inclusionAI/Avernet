// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const closes: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closes.splice(0)) await close(); vi.restoreAllMocks(); });
const tick = () => new Promise<void>(resolve => setImmediate(resolve));

async function fixture(status = 'running', engine = 'native') {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, engine TEXT, status TEXT, retry_epoch INTEGER,
    started_at INTEGER, completed_at INTEGER, state_json TEXT, result_json TEXT,
    input_json TEXT, total_duration_ms INTEGER, total_token_usage INTEGER,
    current_phase TEXT, succeeded_count INTEGER, failed_count INTEGER, gmt_modified INTEGER
  ); CREATE TABLE node_executions (flow_id TEXT, status TEXT);`);
  raw.prepare(`INSERT INTO flow_runs (flow_id,engine,status,retry_epoch,started_at,state_json)
    VALUES ('run',?,?,2,?,?)`).run(engine, status, Math.floor(Date.now() / 1000) - 7395,
    JSON.stringify({ effectiveFlowTimeoutMinutes: 1440 }));
  const db = new SqliteDatabase(raw);
  const onTerminal = vi.fn(async () => {});
  const app = express(); app.use(express.json());
  app.use('/runs', createInternalRunsRouter(new FlowRunRepository(db), null, undefined, { onTerminal }));
  const server = await new Promise<Server>(resolve => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  closes.push(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await db.close(); });
  const put = async (suffix: string, body: unknown) => {
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/runs/run/${suffix}`, {
      method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    await response.json(); await tick(); await tick();
    return response.status;
  };
  return { raw, put, onTerminal, row: () => raw.prepare('SELECT * FROM flow_runs').get() };
}

const timeout = { reason: 'flow_timeout', ranForSecs: 7395, timeoutSecs: 7200 };
const wrappedTimeout = JSON.stringify({ status: 'failed', phase: 'P1', durationMs: 7395000,
  failedNodes: [], message: JSON.stringify(timeout), nodeSummary: 'node:running' });

describe('deployed watchdog status-before-completion bypass', () => {
  it('rejects the no-reason preliminary fail and the wrapped old-budget completion, without observing either', async () => {
    const f = await fixture(); const before = f.row();
    expect(await f.put('status', { status: 'failed' })).toBe(409);
    expect(f.row()).toEqual(before);
    expect(await f.put('completion', { status: 'failed', result_json: wrappedTimeout,
      total_duration_ms: 7395000, succeeded_count: 0, failed_count: 0 })).toBe(409);
    expect(f.row()).toEqual(before);
    expect(f.onTerminal).not.toHaveBeenCalled();
  });

  it('still settles ordinary business failure through completion after rejecting the best-effort status mirror', async () => {
    const f = await fixture();
    expect(await f.put('status', { status: 'failed' })).toBe(409);
    const completion = { status: 'failed', result_json: JSON.stringify({ status: 'failed', phase: 'P1',
      message: 'executor unavailable', failedNodes: [{ nodeId: 'node', error: 'executor unavailable' }] }),
      completed_at: Math.floor(Date.now() / 1000), total_duration_ms: 30000, succeeded_count: 0, failed_count: 1 };
    expect(await f.put('completion', completion)).toBe(200);
    expect(f.row()).toMatchObject({ status: 'failed', failed_count: 1, result_json: completion.result_json });
    expect(f.onTerminal).toHaveBeenCalledTimes(1);
    // A lost HTTP response must not turn a transport retry into another notification.
    expect([200, 409]).toContain(await f.put('completion', completion));
    expect(f.onTerminal).toHaveBeenCalledTimes(1);
  });

  it.each(['native', 'shared'].flatMap(engine => ['succeeded', 'cancelled', 'canceled'].map(status => [engine, status])))('does not overwrite a %s %s via plain failure or wrapped timeout', async (engine, status) => {
    const f = await fixture(status, engine); const before = f.row();
    expect(await f.put('status', { status: 'failed' })).toBe(409);
    expect(await f.put('completion', { status: 'failed', result_json: 'ordinary error',
      succeeded_count: 0, failed_count: 1 })).toBe(409);
    expect(await f.put('completion', { status: 'failed', result_json: wrappedTimeout,
      succeeded_count: 0, failed_count: 1 })).toBe(409);
    expect(f.row()).toEqual(before);
    expect(f.onTerminal).not.toHaveBeenCalled();
  });
});

// The dedicated watchdog endpoint must not treat its name, phase, or evidence
// shape as authority for the timeout budget either.
it.each([1440, 0, null, -1, '120', false, {}])('HTTP watchdog fails closed for saved budget %j despite a usable legacy snapshot', async budget => {
  const f = await fixture();
  f.raw.prepare('UPDATE flow_runs SET state_json = ?').run(JSON.stringify({
    effectiveFlowTimeoutMinutes: budget, workflowSnapshot: { flowTimeoutMinutes: 120, timeoutMinutes: 120 },
  }));
  const before = f.row();
  const status = await f.put('fail-if-running', { reason: JSON.stringify(timeout), current_phase: 'timeout',
    total_duration_ms: 7395000, completed_at: Math.floor(Date.now() / 1000) + 100000 });
  expect(status).toBe(200);
  expect(f.row()).toEqual(before);
  expect(f.onTerminal).not.toHaveBeenCalled();
});

it.each(['FAILED', 'Failed', 'failed ', ' failed'])('rejects noncanonical failure status %j instead of bypassing timeout policy', async status => {
  const f = await fixture('succeeded'); const before = f.row();
  expect(await f.put('status', { status })).toBeGreaterThanOrEqual(400);
  expect(f.row()).toEqual(before);
  expect(await f.put('completion', { status, result_json: wrappedTimeout, succeeded_count: 0, failed_count: 1 })).toBeGreaterThanOrEqual(400);
  expect(f.row()).toEqual(before);
  expect(f.onTerminal).not.toHaveBeenCalled();
});

it.each([undefined, 2])('observes the first settlement of an already-failed but uncompleted row once (epoch %s)', async retry_epoch => {
  const f = await fixture('failed');
  const completion = { status: 'failed', result_json: 'ordinary business failure', completed_at: Math.floor(Date.now() / 1000),
    retry_epoch, succeeded_count: 0, failed_count: 1 };
  expect(await f.put('completion', completion)).toBe(200);
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
  expect([200, 409]).toContain(await f.put('completion', completion));
  expect(f.onTerminal).toHaveBeenCalledTimes(1);
});
