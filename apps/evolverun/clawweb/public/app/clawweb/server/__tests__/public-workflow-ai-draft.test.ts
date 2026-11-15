import { afterEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import type { Server } from 'node:http';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import type { ClawWebLifecycle } from '../bootstrap.js';
const state = vi.hoisted(() => ({ db: null as IDatabase | null, reconcile: vi.fn(async () => 0), close: vi.fn() }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: () => state.close(),
}));
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('../workflow-ai-draft-baas-execution.js', () => ({ createWorkflowAiDraftBaasExecution: () => ({
  resolveTarget: async () => ({ botId: 'autoheal:owner' }), dispatch: async () => ({ jobId: 'job' }), poll: async () => ({ status: 'running' }),
}) }));
vi.mock('../workflow-ai-draft-runtime.js' , async original => {
  const actual = await original<typeof import('../workflow-ai-draft-runtime.js')>();
  return { createWorkflowAiDraftRuntime: (...args: Parameters<typeof actual.createWorkflowAiDraftRuntime>) => ({ ...actual.createWorkflowAiDraftRuntime(...args), monitor: { reconcile: () => state.reconcile() } }) };
});
import { createClawWebBootstrap } from '../public-bootstrap.js';
let lifecycle: ClawWebLifecycle | void;
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  await state.db?.close(); state.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs(); state.close.mockReset(); state.reconcile.mockReset();
});
it('mounts the public capability route independently from Repair, enforces verified principal/ACL, and drains timer before DB close', async () => {
  const db = new SqliteDatabase(new Database(':memory:')); state.db = db;
  await runMigrations(db, 'sqlite');
  await db.exec(`INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, owner_id, gmt_create, gmt_modified)
    VALUES ('flow', 'pack', '{}', 'flow', 'editor', 1, 1)`);
  await db.exec(`INSERT INTO bot_workflow_permissions (bot_owner_id, workflow_id, can_view, can_execute, can_edit, gmt_create, gmt_modified)
    VALUES ('editor', 'flow', 1, 1, 1, 1, 1)`);
  for (const key of ['CLAWWEB_REPAIR_AIS_SNAPSHOT_ID', 'CLAWWEB_REPAIR_CLAWFLOWS_GIT_DIR']) vi.stubEnv(key, undefined);
  vi.stubEnv('PORT', '0');
  let server!: Server;
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) { server = listen.apply(this, args); return server; });
  const timers: Array<() => void> = [], realInterval = global.setInterval;
  vi.spyOn(global, 'setInterval').mockImplementation(((callback: () => void, ms?: number, ...args: unknown[]) => {
    if (ms === 15_000) { timers.push(callback); return { unref() {} } as NodeJS.Timeout; }
    return realInterval(callback, ms, ...args);
  }) as typeof setInterval);
  lifecycle = await createClawWebBootstrap().start({ environment: 'dev', profile: 'public', config: { auth: { admins: ['forged-admin'] } }, configSources: [] });
  if (!server.listening) await once(server, 'listening');
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const url = base + '/api/workflows/flow/ai-drafts/capabilities';
  const editor = await fetch(url, { headers: { cookie: 'staff_id=editor' } });
  expect(editor.status).toBe(401);
  const local = await fetch(url); expect(local.status).toBe(200);
  expect(await local.json()).toEqual({ available: true });
  expect((await fetch(url, { headers: { cookie: 'staff_id=denied' } })).status).toBe(401);
  expect((await fetch(url, { headers: { cookie: 'staff_id=forged-admin' } })).status).toBe(401);
  expect((await fetch(url, { headers: { cookie: 'userId=editor' } })).status).toBe(401);
  expect((await fetch(url, { headers: { origin: 'https://evil.example' } })).status).toBe(403);
  expect((await fetch(url)).status).toBe(200); // fixed local developer admin
  let release!: () => void;
  state.reconcile.mockImplementation(() => new Promise<number>(resolve => { release = () => resolve(0); }));
  timers[0](); expect(state.reconcile).toHaveBeenCalledTimes(1);
  const stopped = lifecycle!.stop!();
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(state.close).not.toHaveBeenCalled();
  release(); await stopped; lifecycle = undefined;
  expect(state.close).toHaveBeenCalledTimes(1);
});
