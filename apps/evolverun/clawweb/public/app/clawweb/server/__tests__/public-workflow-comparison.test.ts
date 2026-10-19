import { afterEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { once } from 'node:events';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import type { ClawWebLifecycle } from '../bootstrap.js';
const state = vi.hoisted(() => ({ db: null as IDatabase | null }));
vi.mock('@avernet/clawweb-shared/server/db', async importOriginal => ({
  ...await importOriginal<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: vi.fn(),
}));
// Isolate unrelated engines, schedulers and AIS; use real workflow routes and DB.
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('../repair-ais-execution.js', () => ({ resolveRepairAisConfiguration: async () => null }));
import { createClawWebBootstrap } from '../public-bootstrap.js';
let lifecycle: ClawWebLifecycle | void;
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  if (state.db) await state.db.close(); state.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});
it('serves exact deployment comparison and provenance through the actual public bootstrap with workflow authorization', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw); state.db = db;
  await runMigrations(db, 'sqlite');
  raw.exec(`INSERT INTO workflow_deploy_history (workflow_id, pack_id, deploy_number, version, action, is_active, tag_name, spec_json, gmt_create)
    VALUES ('wf','pack',1,1,'deploy',0,'deploy/wf/#1','{}',1000000000),
      ('wf','pack',2,1,'deploy',1,'deploy/wf/#2','{}',1000100000);
    INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, workflow_deploy_number) VALUES
      ('old','wf','failed',1000000001,1),('new','wf','succeeded',1000100001,2);`);
  vi.stubEnv('PORT', '0');
  let server: Server | undefined;
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment: 'dev', profile: 'public', config: { auth: { admins: ['admin'] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}/api/workflows/wf`;
  const query = '/version-comparison?baseline=1&current=2';
  expect((await fetch(base + query)).status).toBe(401);
  expect((await fetch(base + query, { headers: { 'X-User-Id': 'outsider' } })).status).toBe(403);
  const response = await fetch(base + query, { headers: { 'X-User-Id': 'admin' } });
  expect(response.status).toBe(200);
  expect((await response.json()).data).toMatchObject({ status: 'ready', differencePercentagePoints: 100,
    baseline: { deployNumber: 1, terminalRuns: 1 }, current: { deployNumber: 2, terminalRuns: 1 } });
  const history = await fetch(base + '/history', { headers: { 'X-User-Id': 'admin' } });
  expect(history.status).toBe(200);
  expect((await history.json()).history[0]).toMatchObject({ deployNumber: 2, source: { kind: 'unknown' } });
});
