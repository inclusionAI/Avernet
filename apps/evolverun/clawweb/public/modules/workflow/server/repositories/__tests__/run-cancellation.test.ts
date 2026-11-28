// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';
import { NodeExecutionRepository } from '../node-execution-repository.js';

function fixture(status: string, epoch: number | null | 'absent') {
  const raw = new Database(':memory:');
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, ${epoch === 'absent' ? '' : 'retry_epoch INTEGER,'}
    state_json TEXT DEFAULT '{"effectiveFlowTimeoutMinutes":1}', started_at INTEGER, completed_at INTEGER, gmt_modified INTEGER, result_json TEXT, input_json TEXT,
    total_duration_ms INTEGER, total_token_usage INTEGER, current_phase TEXT, succeeded_count INTEGER, failed_count INTEGER
  );
  CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT, duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
  INSERT INTO node_executions VALUES ('flow', 'failed', 'original error', 10, 999, 999);`);
  raw.prepare(`INSERT INTO flow_runs (flow_id, status, started_at, completed_at, gmt_modified, result_json, failed_count${epoch === 'absent' ? '' : ', retry_epoch'})
    VALUES ('flow', ?, 123, ?, 999, 'user aborted', 1${epoch === 'absent' ? '' : ', ?'})`)
    .run(...(epoch === 'absent' ? [status, status === 'running' ? null : 999] : [status, status === 'running' ? null : 999, epoch]));
  const db = new SqliteDatabase(raw);
  return { raw, db, runs: new FlowRunRepository(db), snapshot: () => raw.prepare('SELECT * FROM flow_runs').get() };
}

for (const cancelled of ['cancelled', 'canceled']) {
  describe(cancelled, () => {
    for (const stored of ['absent', null, 0, 2] as const) {
      const epochs = stored === 'absent' ? [undefined] : [undefined, 0, 2];
      for (const supplied of epochs) {
        it(`rejects late completion, status and watchdog writes (stored=${stored}, supplied=${supplied})`, async () => {
          const { db, runs, snapshot } = fixture(cancelled, stored);
          try {
            const before = snapshot();
            for (const status of ['failed', 'succeeded', 'running', 'waiting', 'cancelled', 'canceled']) {
              expect(await runs.updateCompletion('flow', {
                status, completedAt: 2000, retryEpoch: supplied, resultJson: 'late engine result', succeededCount: 2, failedCount: 0,
              })).toBe(false);
              expect(await runs.updateStatus('flow', status, supplied)).toBe(false);
              expect(snapshot()).toEqual(before);
            }
            expect(await runs.markFailedIfRunning('flow', {
              reason: JSON.stringify({ reason: 'flow_timeout', timeoutSecs: 60, ranForSecs: 61 }), currentPhase: 'timeout', completedAt: 2000, retryEpoch: supplied,
            })).toBe(false);
            expect(snapshot()).toEqual(before);
          } finally { await db.close(); }
        });
      }
      it(`rejects legacy failed-node retry without resetting node history (stored=${stored})`, async () => {
        const { db, raw, runs, snapshot } = fixture(cancelled, stored);
        try {
          const before = snapshot();
          const nodes = raw.prepare('SELECT * FROM node_executions').all();
          const reset = vi.fn((tx) => new NodeExecutionRepository(tx).resetFailedByFlowId('flow', tx));
          expect(await runs.resetFailedForRetry('flow', reset)).toEqual({ status: 'conflict' });
          expect(reset).not.toHaveBeenCalled();
          expect(snapshot()).toEqual(before);
          expect(raw.prepare('SELECT * FROM node_executions').all()).toEqual(nodes);
        } finally { await db.close(); }
      });
      if (stored !== 'absent') {
        it(`rejects next-epoch and replayed retry-resume (stored=${stored})`, async () => {
          const { db, runs, snapshot } = fixture(cancelled, stored);
          try {
            const before = snapshot();
            expect(await runs.resumeForRetry('flow', (stored ?? 0) + 1)).toBe('conflict');
            if ((stored ?? 0) === 0) await expect(runs.resumeForRetry('flow', 0)).rejects.toThrow(/epoch/);
            else expect(await runs.resumeForRetry('flow', stored!)).toBe('conflict');
            expect(snapshot()).toEqual(before);
          } finally { await db.close(); }
        });
      }
    }
  });
}

it('a real user abort wins over no-epoch late failure and reopening attempts', async () => {
  const { db, runs, snapshot } = fixture('running', 2);
  try {
    expect(await runs.abort('flow', 'stop now', 2)).toBe(true);
    const aborted = snapshot();
    expect(await runs.updateCompletion('flow', { status: 'failed', completedAt: 2000, resultJson: 'late failure', succeededCount: 0, failedCount: 1 })).toBe(false);
    expect(await runs.updateStatus('flow', 'running')).toBe(false);
    expect(await runs.resumeForRetry('flow', 3)).toBe('conflict');
    expect(snapshot()).toEqual(aborted);
  } finally { await db.close(); }
});

it.each(['absent', null, 0, 2] as const)('failed retry requires a coordinated next epoch (epoch=%s)', async (epoch) => {
  const { db, raw, runs } = fixture('failed', epoch);
  try {
    const before = raw.prepare('SELECT * FROM flow_runs').get();
    expect(await runs.resetFailedForRetry('flow', tx => new NodeExecutionRepository(tx).resetFailedByFlowId('flow', tx)))
      .toEqual({ status: 'conflict' });
    expect(raw.prepare('SELECT * FROM flow_runs').get()).toEqual(before);
    if (epoch === 'absent') {
      await expect(runs.resumeForRetry('flow', 1)).rejects.toThrow(/retry_epoch/);
    } else {
      const nextEpoch = (epoch ?? 0) + 1;
      expect(await runs.resumeForRetry('flow', nextEpoch)).toBe('resumed');
      expect(await runs.updateCompletion('flow', { status: 'succeeded', completedAt: 2000, succeededCount: 1, failedCount: 0, retryEpoch: nextEpoch })).toBe(true);
      expect(raw.prepare('SELECT status, started_at FROM flow_runs').get()).toEqual({ status: 'succeeded', started_at: 123 });
    }
    expect(raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'failed' });
  } finally { await db.close(); }
});

it('never invokes a legacy reset callback even when it could mutate parent and children', async () => {
  const { db, raw, runs, snapshot } = fixture('failed', 0);
  try {
    const before = snapshot();
    const beforeNodes = raw.prepare('SELECT * FROM node_executions').all();
    const reset = vi.fn(async (tx) => {
      const count = await new NodeExecutionRepository(tx).resetFailedByFlowId('flow', tx);
      await tx.exec("UPDATE flow_runs SET status = 'cancelled' WHERE flow_id = ?", ['flow']);
      return count;
    });
    expect(await runs.resetFailedForRetry('flow', reset)).toEqual({ status: 'conflict' });
    expect(reset).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(before);
    expect(raw.prepare('SELECT * FROM node_executions').all()).toEqual(beforeNodes);
  } finally { await db.close(); }
});
