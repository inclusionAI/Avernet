import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '../../../../../shared/server/db.js';
import { FlowRunRepository } from '../flow-run-repository.js';
import { NodeExecutionRepository } from '../node-execution-repository.js';

it('rolls back legacy node resets if the run cannot transition', async () => {
  const raw = new Database(':memory:');
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT, failed_count INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO flow_runs VALUES ('flow', 'failed', 1, 999, 999);
    CREATE TABLE node_executions (flow_id TEXT, status TEXT, error_text TEXT, duration_ms INTEGER, completed_at INTEGER, gmt_modified INTEGER);
    INSERT INTO node_executions VALUES ('flow', 'failed', 'error', 10, 999, 999);
    CREATE TRIGGER fail_retry BEFORE UPDATE OF status ON flow_runs
      WHEN NEW.status = 'running' BEGIN SELECT RAISE(ABORT, 'run update failed'); END`);
  const db = new SqliteDatabase(raw);
  try {
    const runs = new FlowRunRepository(db);
    const nodes = new NodeExecutionRepository(db);
    await expect(runs.resetFailedForRetry('flow', tx => nodes.resetFailedByFlowId('flow', tx))).rejects.toThrow('run update failed');
    expect(raw.prepare('SELECT status FROM flow_runs').get()).toEqual({ status: 'failed' });
    expect(raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'failed' });
  } finally { await db.close(); }
});
