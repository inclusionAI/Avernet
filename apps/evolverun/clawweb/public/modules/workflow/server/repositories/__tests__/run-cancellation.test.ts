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
    started_at INTEGER, completed_at INTEGER, gmt_modified INTEGER, result_json TEXT, input_json TEXT,
    total_duration_ms INTEGER, total_token_usage INTEGER, current_phase TEXT, succeeded_count INTEGER, failed_count INTEGER
  );
  CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT, duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
  INSERT INTO node_executions VALUES ('flow', 'failed', 'original error', 10, 999, 999);`);
  raw.prepare(`INSERT INTO flow_runs (flow_id, status, started_at, completed_at, gmt_modified, result_json, failed_count${epoch === 'absent' ? '' : ', retry_epoch'})
    VALUES ('flow', ?, 123, 999, 999, 'user aborted', 1${epoch === 'absent' ? '' : ', ?'})`)
    .run(...(epoch === 'absent' ? [status] : [status, epoch]));
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
              reason: 'late timeout', currentPhase: 'timeout', completedAt: 2000, retryEpoch: supplied,
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
            expect(await runs.resumeForRetry('flow', stored ?? 0)).toBe('conflict');
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
    expect(await runs.abort('flow', 'stop now')).toBe(true);
    const aborted = snapshot();
    expect(await runs.updateCompletion('flow', { status: 'failed', completedAt: 2000, resultJson: 'late failure', succeededCount: 0, failedCount: 1 })).toBe(false);
    expect(await runs.updateStatus('flow', 'running')).toBe(false);
    expect(await runs.resumeForRetry('flow', 3)).toBe('conflict');
    expect(snapshot()).toEqual(aborted);
  } finally { await db.close(); }
});

it.each(['absent', null, 0, 2] as const)('ordinary failed legacy retry stays available (epoch=%s)', async (epoch) => {
  const { db, raw, runs } = fixture('failed', epoch);
  try {
    expect(await runs.resetFailedForRetry('flow', tx => new NodeExecutionRepository(tx).resetFailedByFlowId('flow', tx)))
      .toEqual({ status: 'updated', resetCount: 1 });
    expect(await runs.updateStatus('flow', 'running')).toBe(true);
    expect(await runs.updateCompletion('flow', { status: 'succeeded', completedAt: 2000, succeededCount: 1, failedCount: 0 })).toBe(true);
    expect(raw.prepare('SELECT status, started_at FROM flow_runs').get()).toEqual({ status: 'succeeded', started_at: 123 });
  } finally { await db.close(); }
});

it('rolls back node resets if the final guarded run write loses eligibility', async () => {
  const { db, raw, runs, snapshot } = fixture('failed', 0);
  try {
    const before = snapshot();
    const beforeNodes = raw.prepare('SELECT * FROM node_executions').all();
    expect(await runs.resetFailedForRetry('flow', async tx => {
      const count = await new NodeExecutionRepository(tx).resetFailedByFlowId('flow', tx);
      // Fault injection after the child write proves a rejected final CAS must
      // throw through the transaction before mapping back to a conflict.
      await tx.exec("UPDATE flow_runs SET status = 'cancelled' WHERE flow_id = ?", ['flow']);
      return count;
    })).toEqual({ status: 'conflict' });
    expect(snapshot()).toEqual(before);
    expect(raw.prepare('SELECT * FROM node_executions').all()).toEqual(beforeNodes);
  } finally { await db.close(); }
});
