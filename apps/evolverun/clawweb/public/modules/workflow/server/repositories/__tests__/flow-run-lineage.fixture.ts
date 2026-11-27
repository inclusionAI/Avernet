import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { vi } from 'vitest';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';
import { NodeExecutionRepository } from '../node-execution-repository.js';
import { RunReadRepository } from '../run-read-repository.js';
import { RunSubworkflowsRepository } from '../run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../../services/run-subworkflows-service.js';
import { createRunsRouter } from '../../routes/runs.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';

export async function lineageFixture(guarded = true) {
  const raw = new Database(':memory:');
  raw.function('GREATEST', (a: number, b: number) => Math.max(a, b));
  const db = new SqliteDatabase(raw);
  await runMigrations(db, 'sqlite');
  const repo = new FlowRunRepository(db);
  const permissions = { resolveRunViewScope: async () => 'all' as const,
    listRunViewScopes: async () => new Map([['wf', 'all' as const]]) };
  const authorize = vi.fn(async () => true);
  const authenticate = vi.fn(async () => ({ botId: 'bot', ownerId: 'owner' }));
  const app = express(); app.use(express.json());
  app.use((req, _res, next) => { req.isAdmin = true; next(); });
  app.use('/api/internal/runs', createInternalRunsRouter(repo, new NodeExecutionRepository(db), undefined, {}, { authenticate, authorize }));
  const reader = guarded ? new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions) : null;
  app.use('/api/runs', createRunsRouter(repo, new NodeExecutionRepository(db), null, null, null, null, null, null, null, null, reader));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const request = async (path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') => {
    const response = await fetch(base + path, { method, headers: { 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  };
  const close = async () => { await new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }); await db.close(); };
  return { raw, db, repo, request, authorize, authenticate, scoped: new RunReadRepository(db, permissions), close };
}
