import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';

it('rejects delayed completion and watchdog writes from the previous retry attempt', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, retry_epoch INTEGER NOT NULL DEFAULT 0,
    completed_at INTEGER, gmt_modified INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
    current_phase TEXT, result_json TEXT, input_json TEXT, succeeded_count INTEGER, failed_count INTEGER
  );
  INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES ('flow', 'failed', 0);`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    expect(await runs.resumeForRetry('flow', 1)).toBe('resumed');
    expect(await runs.resumeForRetry('flow', 1)).toBe('resumed'); // lost HTTP response
    expect(await runs.updateCompletion('flow', {
      status: 'succeeded', completedAt: 10, succeededCount: 0, failedCount: 0, retryEpoch: 0,
    })).toBe(false);
    expect(await runs.markFailedIfRunning('flow', {
      reason: 'old timeout', currentPhase: 'timeout', completedAt: 11, retryEpoch: 0,
    })).toBe(false);
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'running', retry_epoch: 1 });
    expect(await runs.updateCompletion('flow', {
      status: 'succeeded', completedAt: 12, succeededCount: 0, failedCount: 0, retryEpoch: 1,
    })).toBe(true);
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'succeeded', retry_epoch: 1 });
  } finally { await db.close(); }
});

it('accepts nullable legacy epochs and never repeats an older retry', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, retry_epoch INTEGER DEFAULT NULL,
    completed_at INTEGER, gmt_modified INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
    current_phase TEXT, result_json TEXT, input_json TEXT, succeeded_count INTEGER, failed_count INTEGER
  );
  INSERT INTO flow_runs (flow_id, status) VALUES ('legacy', 'running');
  INSERT INTO flow_runs (flow_id, status) VALUES ('legacy-watchdog', 'running');
  INSERT INTO flow_runs (flow_id, status) VALUES ('retry', 'failed');`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    expect(await runs.updateCompletion('legacy', {
      status: 'succeeded', completedAt: 10, succeededCount: 0, failedCount: 0,
    })).toBe(true);
    expect(await runs.markFailedIfRunning('legacy-watchdog', {
      reason: 'timeout', currentPhase: 'timeout', completedAt: 10,
    })).toBe(true);
    expect(await runs.resumeForRetry('retry', 2)).toBe('conflict');
    expect(await runs.resumeForRetry('retry', 1)).toBe('resumed');
    expect(await runs.resumeForRetry('retry', 2)).toBe('resumed');
    expect(await runs.resumeForRetry('retry', 1)).toBe('conflict');
    expect(raw.prepare('SELECT retry_epoch FROM flow_runs WHERE flow_id = ?').get('retry')).toEqual({ retry_epoch: 2 });
  } finally { await db.close(); }
});

it('keeps the legacy retry route usable for records written by the epoch plugin', async () => {
  const raw = new Database(':memory:');
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, retry_epoch INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('flow', 'failed', 1, 1, 10, 10);`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    let reset = false;
    const result = await runs.resetFailedForRetry('flow', async () => { reset = true; return 1; });
    expect(result.status).toBe('updated');
    expect(reset).toBe(true);
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'running', retry_epoch: 1 });
  } finally { await db.close(); }
});

it('keeps the legacy failed-node retry available when its epoch is null', async () => {
  const raw = new Database(':memory:');
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, retry_epoch INTEGER DEFAULT NULL,
    failed_count INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('legacy', 'failed', NULL, 1, 10, 10);`);
  const db = new SqliteDatabase(raw);
  try {
    const result = await new FlowRunRepository(db).resetFailedForRetry('legacy', async () => 1);
    expect(result.status).toBe('updated');
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'running', retry_epoch: null });
  } finally { await db.close(); }
});

it('keeps a positive-epoch watchdog claim final without blocking normal failure details', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, retry_epoch INTEGER DEFAULT 0,
    completed_at INTEGER, gmt_modified INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
    current_phase TEXT, result_json TEXT, input_json TEXT, succeeded_count INTEGER, failed_count INTEGER
  );
  INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES ('timed-out', 'running', 1);
  INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES ('normal-failure', 'failed', 1);`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    expect(await runs.markFailedIfRunning('timed-out', {
      reason: 'timeout', currentPhase: 'timeout', completedAt: 10, retryEpoch: 1,
    })).toBe(true);
    expect(await runs.updateCompletion('timed-out', {
      status: 'succeeded', completedAt: 11, succeededCount: 0, failedCount: 0, retryEpoch: 1,
    })).toBe(false);
    expect(await runs.updateCompletion('timed-out', {
      status: 'failed', completedAt: 11, succeededCount: 0, failedCount: 0, retryEpoch: 1,
    })).toBe(false);
    expect(await runs.updateStatus('timed-out', 'running', 1)).toBe(false);
    expect(await runs.updateStatus('timed-out', 'waiting', 1)).toBe(false);
    expect(raw.prepare('SELECT status, result_json FROM flow_runs WHERE flow_id = ?').get('timed-out'))
      .toEqual({ status: 'failed', result_json: 'timeout' });
    expect(await runs.updateCompletion('normal-failure', {
      status: 'failed', completedAt: 12, succeededCount: 0, failedCount: 0, retryEpoch: 1,
    })).toBe(true);
  } finally { await db.close(); }
});

it('rejects stale status writes after a retry while preserving epoch-zero status updates', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, retry_epoch INTEGER DEFAULT 0,
    completed_at INTEGER, gmt_modified INTEGER
  );
  INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES ('retried', 'running', 1);
  INSERT INTO flow_runs (flow_id, status) VALUES ('legacy', 'failed');`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    expect(await runs.updateStatus('retried', 'waiting', 0)).toBe(false);
    expect(await runs.updateStatus('retried', 'failed', 0)).toBe(false);
    expect(await runs.updateStatus('retried', 'waiting', 1)).toBe(true);
    expect(await runs.updateStatus('retried', 'running', 1)).toBe(true);
    expect(await runs.updateStatus('legacy', 'running')).toBe(true);
    expect(raw.prepare('SELECT status FROM flow_runs WHERE flow_id = ?').get('retried')).toEqual({ status: 'running' });
  } finally { await db.close(); }
});

it('keeps the original status and completion contract usable with null, zero, and positive stored epochs', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT NOT NULL, retry_epoch INTEGER DEFAULT NULL,
    started_at INTEGER, completed_at INTEGER, gmt_modified INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
    current_phase TEXT, result_json TEXT, input_json TEXT, succeeded_count INTEGER, failed_count INTEGER
  );`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    for (const epoch of [null, 0, 2]) {
      for (const status of ['failed', 'succeeded', 'cancelled', 'blocked', 'waiting']) {
        const id = `${epoch}-${status}`;
        raw.prepare('INSERT INTO flow_runs (flow_id, status, retry_epoch, started_at, completed_at) VALUES (?, ?, ?, 1, 100)').run(id, status, epoch);
        expect(await runs.updateStatus(id, 'running')).toBe(true);
        expect(raw.prepare('SELECT status, started_at, completed_at, retry_epoch FROM flow_runs WHERE flow_id = ?').get(id))
          .toEqual({ status: 'running', started_at: 1, completed_at: null, retry_epoch: epoch === 2 ? 0 : epoch });
        expect(await runs.updateCompletion(id, { status: 'succeeded', completedAt: 200, succeededCount: 1, failedCount: 0 })).toBe(true);
      }
    }
    raw.prepare('INSERT INTO flow_runs (flow_id, status, retry_epoch) VALUES (?, ?, ?)').run('watchdog', 'running', 2);
    expect(await runs.markFailedIfRunning('watchdog', { reason: 'timeout', currentPhase: 'P1', completedAt: 200 })).toBe(true);
    expect(await runs.markFailedIfRunning('watchdog', { reason: 'timeout', currentPhase: 'P1', completedAt: 200 })).toBe(false);
  } finally { await db.close(); }
});

it('allows deployed epoch clients to request retry from previously supported terminal states', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, retry_epoch INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('succeeded', 'succeeded', 2, 100, 100), ('cancelled', 'cancelled', 2, 100, 100);`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    expect(await runs.resumeForRetry('succeeded', 3)).toBe('resumed');
    expect(await runs.resumeForRetry('cancelled', 3)).toBe('resumed');
  } finally { await db.close(); }
});

it('keeps ordinary retry, completion, and timeout usable on a schema without retry_epoch', async () => {
  const raw = new Database(':memory:');
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT, started_at INTEGER, completed_at INTEGER, gmt_modified INTEGER,
    total_duration_ms INTEGER, total_token_usage INTEGER, current_phase TEXT, result_json TEXT, input_json TEXT,
    succeeded_count INTEGER, failed_count INTEGER
  ); INSERT INTO flow_runs (flow_id, status, started_at, failed_count) VALUES ('flow', 'failed', 1, 1);`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    expect((await runs.resetFailedForRetry('flow', async () => 1)).status).toBe('updated');
    expect(await runs.updateStatus('flow', 'running')).toBe(true);
    expect(await runs.markFailedIfRunning('flow', { reason: 'timeout', currentPhase: 'P1', completedAt: 10 })).toBe(true);
    expect(await runs.updateStatus('flow', 'running')).toBe(true);
    expect(await runs.updateCompletion('flow', { status: 'succeeded', completedAt: 20, succeededCount: 1, failedCount: 0 })).toBe(true);
    expect(raw.prepare('SELECT started_at, status FROM flow_runs').get()).toEqual({ started_at: 1, status: 'succeeded' });
  } finally { await db.close(); }
});

it('invalidates the preceding explicit epoch when the original status API reopens a run', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT, retry_epoch INTEGER, started_at INTEGER, completed_at INTEGER,
    gmt_modified INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER, current_phase TEXT,
    result_json TEXT, input_json TEXT, succeeded_count INTEGER, failed_count INTEGER
  );`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    for (const status of ['failed', 'blocked', 'waiting', 'succeeded', 'cancelled']) {
      raw.prepare('INSERT INTO flow_runs (flow_id, status, retry_epoch, started_at, completed_at, result_json) VALUES (?, ?, 2, 1, 100, ?)')
        .run(status, status, 'previous result');
      expect(await runs.updateStatus(status, 'running')).toBe(true);
      expect(await runs.updateCompletion(status, { status: 'failed', completedAt: 200, resultJson: 'late old result', succeededCount: 0, failedCount: 1, retryEpoch: 2 })).toBe(false);
      expect(await runs.markFailedIfRunning(status, { reason: 'old timeout', currentPhase: 'P1', completedAt: 200, retryEpoch: 2 })).toBe(false);
      expect(await runs.updateStatus(status, 'failed', 2)).toBe(false);
      expect(await runs.resumeForRetry(status, 3)).toBe('conflict');
      expect(raw.prepare('SELECT status, retry_epoch, started_at, completed_at, result_json FROM flow_runs WHERE flow_id = ?').get(status))
        .toEqual({ status: 'running', retry_epoch: 0, started_at: 1, completed_at: null, result_json: 'previous result' });
      expect(await runs.updateCompletion(status, { status: 'succeeded', completedAt: 300, resultJson: 'new result', succeededCount: 1, failedCount: 0 })).toBe(true);
    }
  } finally { await db.close(); }
});

it('does not invalidate an explicit epoch for an already-running status update', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, retry_epoch INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('flow', 'running', 2, NULL, 1);`);
  const db = new SqliteDatabase(raw);
  try {
    expect(await new FlowRunRepository(db).updateStatus('flow', 'running')).toBe(true);
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'running', retry_epoch: 2 });
  } finally { await db.close(); }
});

it('does not fall back to an unguarded status write on other database failures', async () => {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, retry_epoch INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('flow', 'failed', 2, 100, 1);
    CREATE TRIGGER reject_epoch_update BEFORE UPDATE OF retry_epoch ON flow_runs BEGIN SELECT RAISE(ABORT, 'retry write failed'); END;`);
  const db = new SqliteDatabase(raw);
  try {
    await expect(new FlowRunRepository(db).updateStatus('flow', 'running')).rejects.toThrow('retry write failed');
    expect(raw.prepare('SELECT status, retry_epoch, completed_at FROM flow_runs').get())
      .toEqual({ status: 'failed', retry_epoch: 2, completed_at: 100 });
  } finally { await db.close(); }
});
