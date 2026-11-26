import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { vi } from 'vitest';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';

export const NOW_SECONDS = 1_800_000_000;
export const timeoutEvidence = (timeoutSecs = 7200, ranForSecs = 7395, reason = 'flow_timeout') =>
  JSON.stringify({ reason, ranForSecs, timeoutSecs });
export const structuredTimeout = (evidence = timeoutEvidence()) => JSON.stringify({
  status: 'failed', message: evidence, failedNodes: [],
});

export function timeoutFixture() {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(NOW_SECONDS * 1000);
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, status TEXT COLLATE NOCASE NOT NULL, retry_epoch INTEGER,
    started_at INTEGER NOT NULL, state_json TEXT COLLATE NOCASE, completed_at INTEGER,
    result_json TEXT, current_phase TEXT, total_duration_ms INTEGER, total_token_usage INTEGER,
    gmt_modified INTEGER, succeeded_count INTEGER, failed_count INTEGER, input_json TEXT
  ); CREATE TABLE node_executions (flow_id TEXT, status TEXT);`);
  const db = new SqliteDatabase(raw);
  const repo = new FlowRunRepository(db);
  const onTerminal = vi.fn(async () => {});
  let server: Server | undefined;
  function seed(state: unknown = { effectiveFlowTimeoutMinutes: 120 }, options: {
    status?: string; elapsed?: number; epoch?: number | null; stateJson?: string | null;
  } = {}) {
    raw.prepare(`INSERT INTO flow_runs (flow_id, status, retry_epoch, started_at, state_json, result_json,
      completed_at, gmt_modified, succeeded_count, failed_count) VALUES ('flow', ?, ?, ?, ?, 'original', ?, 10, 0, 0)`)
      .run(options.status ?? 'running', options.epoch === undefined ? 0 : options.epoch,
        NOW_SECONDS - (options.elapsed ?? 7400),
        Object.hasOwn(options, 'stateJson') ? options.stateJson! : JSON.stringify(state),
        options.status && ['succeeded', 'cancelled', 'canceled', 'failed'].includes(options.status) ? NOW_SECONDS - 1 : null);
  }
  const row = () => raw.prepare('SELECT * FROM flow_runs').get() as Record<string, unknown>;
  async function http(database: IDatabase = db) {
    const app = express(); app.use(express.json());
    app.use('/runs', createInternalRunsRouter(new FlowRunRepository(database), undefined, undefined, { onTerminal }));
    server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    return async (endpoint: string, body: unknown) => {
      const response = await fetch(`${base}/runs/flow/${endpoint}`, {
        method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await response.json();
      await new Promise<void>(resolve => setImmediate(resolve));
      return { status: response.status, body: data };
    };
  }
  // Return a genuine SQLite SELECT snapshot, then mutate the real database before
  // the repository uses that snapshot. No fake SQL results or transaction mocks.
  function afterPolicyRead(mutate: () => void): { db: IDatabase; hit: () => boolean } {
    let hit = false;
    const wrapped: IDatabase = {
      dbType: db.dbType, dialect: db.dialect,
      query: async <T>(sql: string, params?: unknown[]) => {
        const rows = await db.query<T>(sql, params);
        if (!hit && /^\s*SELECT\b/i.test(sql) && /state_json/i.test(sql) && /FROM flow_runs/i.test(sql)) {
          hit = true; mutate();
        }
        return rows;
      },
      exec: (sql, params) => db.exec(sql, params),
      transaction: fn => db.transaction(fn), close: async () => {},
    };
    return { db: wrapped, hit: () => hit };
  }
  async function close() {
    if (server) await new Promise<void>(resolve => { server!.closeAllConnections(); server!.close(() => resolve()); });
    await db.close(); clock.mockRestore();
  }
  return { raw, db, repo, seed, row, http, onTerminal, afterPolicyRead, close };
}
