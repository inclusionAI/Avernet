import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';

export const priorRetryState = { workflowId: 'wf', retryEpoch: 2, effectiveFlowTimeoutMinutes: 8200,
  nodeStates: { task: { status: 'failed' } }, checkpoint: 'prior' };
export const nextRetryState = { ...priorRetryState, retryEpoch: 3, nodeStates: { task: { status: 'pending' } }, checkpoint: 'next' };
export function retryCheckpointFixture() {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT COLLATE NOCASE PRIMARY KEY, status TEXT COLLATE NOCASE,
    workflow_id TEXT COLLATE NOCASE, retry_epoch INTEGER, state_json TEXT COLLATE NOCASE, params_json TEXT COLLATE NOCASE,
    started_at INTEGER, completed_at INTEGER, current_phase TEXT, result_json TEXT, input_json TEXT,
    total_duration_ms INTEGER, total_token_usage INTEGER, succeeded_count INTEGER, failed_count INTEGER, gmt_modified INTEGER);
    CREATE TABLE node_executions (flow_id TEXT, status TEXT);
    INSERT INTO node_executions VALUES ('flow', 'failed');
    CREATE TABLE retry_audit (epoch INTEGER, status TEXT, state_json TEXT, params_json TEXT);
    CREATE TRIGGER retry_audit AFTER UPDATE ON flow_runs BEGIN
      INSERT INTO retry_audit VALUES (NEW.retry_epoch, NEW.status, NEW.state_json, NEW.params_json); END;`);
  const db = new SqliteDatabase(raw); const repo = new FlowRunRepository(db); const onTerminal = vi.fn();
  let server: Server | undefined;
  function seed(kind: 'canonical' | 'embedded-string' | 'embedded-object' | 'both' | 'none' = 'both') {
    const canonical = kind === 'canonical' || kind === 'both' ? JSON.stringify(priorRetryState) : null;
    const embedded = kind === 'embedded-object' ? priorRetryState : JSON.stringify(priorRetryState);
    const params = kind === 'canonical' || kind === 'none' ? { input: { keep: 7 } } : { input: { keep: 7 }, _clawmind_state: embedded };
    raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
      .run('flow', 'failed', 'wf', 2, canonical, JSON.stringify(params), 123, 456, 'P1', 'old result', 'old input', 10, 20, 1, 1, 99);
  }
  const row = () => raw.prepare('SELECT * FROM flow_runs').get() as Record<string, unknown>;
  async function http(repository: FlowRunRepository | null = repo) {
    const app = express(); app.use(express.json());
    app.use('/runs', createInternalRunsRouter(repository, undefined, undefined, { onTerminal }));
    server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    return async (body: unknown, flowId = 'flow') => {
      const response = await fetch(`${base}/runs/${flowId}/retry-resume`, {
        method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      return { status: response.status, body: await response.json() };
    };
  }
  async function close() {
    if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
    await db.close();
  }
  return { db, repo, raw, seed, row, http, onTerminal, close };
}
