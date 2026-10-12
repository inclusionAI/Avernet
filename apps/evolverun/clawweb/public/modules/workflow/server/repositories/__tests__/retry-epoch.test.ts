import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '../../../../../shared/server/db.js';
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

it('does not let the legacy retry route reset nodes of a generation-fenced run', async () => {
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
    expect(result.status).toBe('conflict');
    expect(reset).toBe(false);
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs').get()).toEqual({ status: 'failed', retry_epoch: 1 });
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
    expect(await runs.updateStatus('retried', 'waiting')).toBe(false);
    expect(await runs.updateStatus('retried', 'failed', 0)).toBe(false);
    expect(await runs.updateStatus('retried', 'waiting', 1)).toBe(true);
    expect(await runs.updateStatus('retried', 'running', 1)).toBe(true);
    expect(await runs.updateStatus('legacy', 'running')).toBe(true);
    expect(raw.prepare('SELECT status FROM flow_runs WHERE flow_id = ?').get('retried')).toEqual({ status: 'running' });
  } finally { await db.close(); }
});
