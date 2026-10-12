// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { sqliteDialect } from '../../../../../shared/server/db/dialect.js';
import { SqliteDatabase } from '../../../../../shared/server/db.js';
import { migrations } from '../../../../../shared/server/schema.js';
import { FlowRunRepository } from '../flow-run-repository.js';
import { NodeExecutionRepository } from '../node-execution-repository.js';

const initial = { mode: 'claim', ownerInstanceId: 'owner', bootId: 'boot1', epoch: 0, seq: 1, elapsedMs: 1200, phase: 'recovering' } as const;
const timeout = { reason: 'timeout', currentPhase: 'timeout', completedAt: 999 };
describe('recovery clock storage contract', () => {
  let raw: Database.Database;
  let db: SqliteDatabase;
  let repo: FlowRunRepository;
  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER, completed_at INTEGER, result_json TEXT, current_phase TEXT, total_duration_ms INTEGER, total_token_usage INTEGER, succeeded_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0, gmt_modified INTEGER);
      INSERT INTO flow_runs (flow_id, status, started_at, completed_at, result_json, current_phase, total_duration_ms) VALUES ('flow', 'running', 123, NULL, NULL, NULL, NULL)`);
    for (const sql of migrations.find(m => m.version === 140)?.sql ?? []) raw.exec(sqliteDialect.renderDdl(sql));
    db = new SqliteDatabase(raw);
    repo = new FlowRunRepository(db);
  });
  afterEach(async () => db.close());
  it('claims only an existing run and returns the persisted clock', async () => {
    await expect(repo.putRecoveryClock('missing', initial)).rejects.toThrow('not found');
    expect(await repo.putRecoveryClock('flow', initial)).toEqual(expect.objectContaining({ bootId: 'boot1', elapsedMs: 1200 }));
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 0, phase: 'recovering' }));
    await expect(repo.putRecoveryClock('flow', { ...initial, ownerInstanceId: 'other' })).rejects.toThrow('conflict');
  });
  it('requires boot CAS and never rolls elapsed or seq backwards', async () => {
    await repo.putRecoveryClock('flow', initial);
    await expect(repo.putRecoveryClock('flow', { ...initial, bootId: 'boot2' })).rejects.toThrow('conflict');
    const next = { ...initial, bootId: 'boot2', expectedBootId: 'boot1', seq: 2 };
    await repo.putRecoveryClock('flow', next);
    expect(await repo.putRecoveryClock('flow', next)).toEqual(expect.objectContaining({ bootId: 'boot2' }));
    await expect(repo.putRecoveryClock('flow', { ...initial, mode: 'checkpoint', seq: 3, phase: 'active' })).rejects.toThrow('conflict');
    await expect(repo.putRecoveryClock('flow', { ...next, mode: 'checkpoint', elapsedMs: 100, seq: 3, phase: 'active' })).rejects.toThrow('conflict');
  });
  it('makes checkpoints idempotent and rejects same-seq changes', async () => {
    await repo.putRecoveryClock('flow', initial);
    const checkpoint = { ...initial, mode: 'checkpoint', seq: 2, elapsedMs: 1300, phase: 'active' };
    await repo.putRecoveryClock('flow', checkpoint);
    expect(await repo.putRecoveryClock('flow', checkpoint)).toEqual(expect.objectContaining({ seq: 2, phase: 'active' }));
    await expect(repo.putRecoveryClock('flow', { ...checkpoint, elapsedMs: 1400 })).rejects.toThrow('conflict');
    await expect(repo.putRecoveryClock('flow', { ...checkpoint, seq: 1 })).rejects.toThrow('conflict');
  });
  it('uses retry request ids once, retains started_at, and blocks stale watchdogs', async () => {
    await repo.putRecoveryClock('flow', initial);
    const active = await repo.putRecoveryClock('flow', { ...initial, mode: 'checkpoint', seq: 2, phase: 'active' });
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending', requestId: 'request1' };
    await repo.putRecoveryClock('flow', retry);
    await repo.putRecoveryClock('flow', retry);
    expect(await repo.markFailedIfRunning('flow', { ...timeout, expectedClock: active })).toBe(false);
    expect(await repo.resetStartedAt('flow', 999)).toBe(false);
    const retried = { ...retry, mode: 'checkpoint', seq: 1, phase: 'active', elapsedMs: 5 };
    await repo.putRecoveryClock('flow', retried);
    expect(await repo.putRecoveryClock('flow', retry)).toEqual(expect.objectContaining({ epoch: 1, seq: 1, phase: 'active' }));
    await expect(repo.putRecoveryClock('flow', { ...retry, epoch: 2 })).rejects.toThrow('conflict');
    expect(raw.prepare('SELECT started_at FROM flow_runs').get()).toEqual({ started_at: 123 });
  });
  it('rejects unguarded, wrong-owner and recovering timeout claims, allowing only current active clock', async () => {
    await repo.putRecoveryClock('flow', initial);
    expect(await repo.markFailedIfRunning('flow', timeout)).toBe(false);
    expect(await repo.markFailedIfRunning('flow', { ...timeout, expectedClock: initial })).toBe(false);
    const active = await repo.putRecoveryClock('flow', { ...initial, mode: 'checkpoint', seq: 2, phase: 'active' });
    expect(await repo.markFailedIfRunning('flow', { ...timeout, expectedClock: { ...active, ownerInstanceId: 'other' } })).toBe(false);
    expect(await repo.markFailedIfRunning('flow', { ...timeout, expectedClock: active })).toBe(true);
    expect(await repo.markFailedIfRunning('flow', { ...timeout, expectedClock: active })).toBe(false);
  });
  it('serializes competing retries without opening two windows', async () => {
    await repo.putRecoveryClock('flow', initial);
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending' };
    const results = await Promise.allSettled([
      repo.putRecoveryClock('flow', { ...retry, requestId: 'a' }),
      repo.putRecoveryClock('flow', { ...retry, requestId: 'b' }),
    ]);
    expect(results.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 1 }));
  });
  it('activates a terminal retry atomically and rejects legacy status/completion during preparation', async () => {
    raw.exec("UPDATE flow_runs SET status = 'failed', completed_at = 999");
    await repo.putRecoveryClock('flow', initial);
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending', requestId: 'manual' };
    await repo.putRecoveryClock('flow', retry);
    expect(await repo.updateStatus('flow', 'running')).toBe(false);
    expect(await repo.updateCompletion('flow', { status: 'succeeded', completedAt: 999, succeededCount: 1, failedCount: 0 })).toBe(false);
    await repo.putRecoveryClock('flow', { ...retry, mode: 'checkpoint', seq: 1, phase: 'active' });
    expect(raw.prepare('SELECT status, started_at, completed_at FROM flow_runs').get()).toEqual({ status: 'running', started_at: 123, completed_at: null });
    expect(await repo.updateCompletion('flow', { status: 'succeeded', completedAt: 1000, succeededCount: 1, failedCount: 0 })).toBe(true);
    expect(await repo.updateStatus('flow', 'running')).toBe(false);
    expect(await repo.markFailedIfRunning('flow', { ...timeout, expectedClock: { ...retry, seq: 1 } })).toBe(false);
  });
  it('serializes a timeout racing a retry preparation and cannot overwrite the new window', async () => {
    await repo.putRecoveryClock('flow', initial);
    const active = await repo.putRecoveryClock('flow', { ...initial, mode: 'checkpoint', seq: 2, phase: 'active' });
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending', requestId: 'racing' };
    await Promise.all([repo.putRecoveryClock('flow', retry), repo.markFailedIfRunning('flow', { ...timeout, expectedClock: active })]);
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 1, phase: 'retry_pending' }));
    expect(raw.prepare('SELECT status FROM flow_runs').get()).toEqual({ status: 'running' });
  });
  it('does not resurrect a cancellation committed after retry preparation', async () => {
    await repo.putRecoveryClock('flow', initial);
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending', requestId: 'cancelled-retry' };
    await repo.putRecoveryClock('flow', retry);
    await db.exec("UPDATE flow_runs SET status = 'cancelled' WHERE flow_id = ?", ['flow']);
    await expect(repo.putRecoveryClock('flow', { ...retry, mode: 'checkpoint', seq: 1, phase: 'active' })).rejects.toThrow('conflict');
  });
  it('rolls back retry receipts when clock persistence fails, then safely replays', async () => {
    await repo.putRecoveryClock('flow', initial);
    raw.exec("CREATE TRIGGER fail_clock BEFORE UPDATE ON flow_recovery_clocks BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END");
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending', requestId: 'durable' };
    await expect(repo.putRecoveryClock('flow', retry)).rejects.toThrow('storage unavailable');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_recovery_retries').get()).toEqual({ n: 0 });
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ epoch: 0 }));
    raw.exec('DROP TRIGGER fail_clock');
    expect(await repo.putRecoveryClock('flow', retry)).toEqual(expect.objectContaining({ epoch: 1 }));
  });
  it('keeps pending retry semantics across boot CAS and ignores delayed old-boot checkpoints', async () => {
    await repo.putRecoveryClock('flow', initial);
    const retry = { ...initial, mode: 'retry', epoch: 1, seq: 0, elapsedMs: 0, phase: 'retry_pending', requestId: 'restart' };
    await repo.putRecoveryClock('flow', retry);
    const resumed = { ...retry, mode: 'claim', bootId: 'boot2', expectedBootId: 'boot1', seq: 1 };
    await repo.putRecoveryClock('flow', resumed);
    await expect(repo.putRecoveryClock('flow', { ...retry, mode: 'checkpoint', seq: 2, phase: 'active' })).rejects.toThrow('conflict');
    await repo.putRecoveryClock('flow', { ...resumed, mode: 'checkpoint', seq: 2, phase: 'active' });
    expect(await repo.getRecoveryClock('flow')).toEqual(expect.objectContaining({ bootId: 'boot2', phase: 'active', epoch: 1 }));
  });
  it('preserves the completion winner against a concurrent timeout', async () => {
    await repo.putRecoveryClock('flow', initial);
    const active = await repo.putRecoveryClock('flow', { ...initial, mode: 'checkpoint', seq: 2, phase: 'active' });
    const results = await Promise.all([
      repo.updateCompletion('flow', { status: 'succeeded', completedAt: 1000, succeededCount: 1, failedCount: 0 }),
      repo.markFailedIfRunning('flow', { ...timeout, expectedClock: active }),
    ]);
    expect(results).toEqual([true, false]);
    expect(raw.prepare('SELECT status FROM flow_runs').get()).toEqual({ status: 'succeeded' });
  });
  it('rejects legacy retry-failed mutations for clock-enabled runs', async () => {
    raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
    raw.exec("UPDATE flow_runs SET status = 'failed', failed_count = 1");
    await repo.putRecoveryClock('flow', initial);
    expect(await repo.resetFailedForRetry('flow', async () => 1)).toEqual({ status: 'clock_conflict' });
    expect(raw.prepare('SELECT status, failed_count FROM flow_runs').get()).toEqual({ status: 'failed', failed_count: 1 });
  });
  it('fences legacy node resets and run status in one transaction', async () => {
    raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
    raw.exec(`CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT,
      duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
      INSERT INTO node_executions VALUES ('flow', 'failed', 'error', 10, 999, 999);
      UPDATE flow_runs SET status = 'failed', failed_count = 1 WHERE flow_id = 'flow'`);
    const nodes = new NodeExecutionRepository(db);
    const reset = () => repo.resetFailedForRetry('flow', tx => nodes.resetFailedByFlowId('flow', tx));

    await repo.putRecoveryClock('flow', initial);
    expect(await reset()).toEqual({ status: 'clock_conflict' });
    expect(raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'failed' });
    expect(raw.prepare('SELECT status, failed_count FROM flow_runs').get()).toEqual({ status: 'failed', failed_count: 1 });
  });
  it('rolls back legacy node resets when the run update fails', async () => {
    raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
    raw.exec(`CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT,
      duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
      INSERT INTO node_executions VALUES ('flow', 'failed', 'error', 10, 999, 999);
      UPDATE flow_runs SET status = 'failed', failed_count = 1 WHERE flow_id = 'flow';
      CREATE TRIGGER fail_legacy_retry BEFORE UPDATE OF status ON flow_runs
      WHEN NEW.status = 'running' BEGIN SELECT RAISE(ABORT, 'run update failed'); END`);
    const nodes = new NodeExecutionRepository(db);
    await expect(repo.resetFailedForRetry('flow', tx => nodes.resetFailedByFlowId('flow', tx))).rejects.toThrow('run update failed');
    expect(raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'failed' });
    expect(raw.prepare('SELECT status, failed_count FROM flow_runs').get()).toEqual({ status: 'failed', failed_count: 1 });
  });
  it('keeps legacy timeout/reset and propagates missing schema errors', async () => {
    expect(await repo.resetStartedAt('flow', 456)).toBe(true);
    expect(await repo.markFailedIfRunning('flow', timeout)).toBe(true);
    raw.exec('DROP TABLE flow_recovery_clocks');
    await expect(repo.getRecoveryClock('flow')).rejects.toThrow();
    await expect(repo.markFailedIfRunning('flow', timeout)).rejects.toThrow();
  });
});
