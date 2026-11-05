import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { generateKeyPairSync, sign } from 'node:crypto';
import { once } from 'node:events';
import type { Server } from 'node:http';
import Database from 'better-sqlite3';
import express from 'express';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import type { ClawWebLifecycle } from '../bootstrap.js';

const state = vi.hoisted(() => ({ db: null as IDatabase | null, publicKeyB64: '' }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: vi.fn(),
  resolveSignatureConfig: () => ({ publicKeyB64: state.publicKeyB64 }),
  resolveFlowControlConfig: () => ({ enabled: true,
    queueGuard: { enabled: true, maxQueueSize: 20, maxEnqueuePerFlow: 1, activeStatuses: ['waiting'] } }),
}));
// Isolate engines/providers, not the real HTTP routers, permissions or migrated storage.
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('../repair-ais-execution.js', () => ({ resolveRepairAisConfiguration: async () => null }));
import { createClawWebBootstrap } from '../public-bootstrap.js';

const keys = generateKeyPairSync('ed25519');
const publicKeyB64 = keys.publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
const insert = { flow_id: 'engine-parent', workflow_id: 'parent-workflow', status: 'running', started_at: 1, state_json: '{}' };
const permission = { botId: 'engine', botOwnerId: 'owner', workflowId: 'child-workflow', permission: 'execute' };
let raw: Database.Database;
let lifecycle: ClawWebLifecycle | void;
let base: string;
function signed(body?: unknown) {
  const timestamp = String(Date.now());
  return { 'X-Timestamp': timestamp, 'X-Signature': sign(null, Buffer.from(`${timestamp}.${body === undefined ? '' : JSON.stringify(body)}`), keys.privateKey).toString('base64') };
}
async function start(environment: 'dev' | 'pre' | 'prod', key = '') {
  state.publicKeyB64 = key;
  let server: Server | undefined;
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment, profile: 'public', config: {}, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  base = `http://127.0.0.1:${(server!.address() as { port: number }).port}/api/internal`;
}
function post(path: string, body: unknown, headers: Record<string, string> = {}) {
  return fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
}
beforeEach(async () => {
  state.publicKeyB64 = ''; raw = new Database(':memory:'); state.db = new SqliteDatabase(raw);
  vi.stubEnv('PORT', '0');
  await runMigrations(state.db, 'sqlite');
  raw.exec(`INSERT INTO bot_workflow_permissions (bot_id,bot_owner_id,workflow_id,env,can_view,can_execute,can_edit)
    VALUES ('engine','owner','child-workflow','dev',1,1,0);`);
});
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  if (state.db) await state.db.close(); state.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it('mounts only the requested internal routers for unsigned dev socket-loopback clients', async () => {
  await start('dev');
  expect((await post('/runs', insert)).status).toBe(201);
  expect(raw.prepare('SELECT status FROM flow_runs WHERE flow_id=?').get('engine-parent')).toEqual({ status: 'running' });
  const group = await fetch(base + '/runs/engine-parent/fanouts/group'); expect(group.status).toBe(200);
  expect(await group.json()).toMatchObject({ success: true, data: { group: null, parentStatus: 'running' } });
  const permissions = await post('/bot-workflow-permissions/check', permission); expect(permissions.status).toBe(200);
  expect(await permissions.json()).toEqual({ allowed: true, hasRecords: true });
  expect((await fetch(base + '/workflows')).status).toBe(404);
});
it.each(['pre', 'prod'] as const)('rejects all engine requests without a configured key in %s, even from loopback', async environment => {
  await start(environment);
  expect((await post('/runs', insert)).status).toBe(503);
  expect((await post('/bot-workflow-permissions/check', permission)).status).toBe(503);
  expect((await fetch(base + '/runs/engine-parent')).status).toBe(503);
  expect(raw.prepare('SELECT COUNT(*) AS count FROM flow_runs').get()).toEqual({ count: 0 });
});
it.each(['dev', 'prod'] as const)('requires real signatures on both mounted routers when a key is configured in %s', async environment => {
  await start(environment, publicKeyB64);
  expect((await post('/runs', insert, { 'X-User-Id': 'admin', 'X-Forwarded-For': '127.0.0.1' })).status).toBe(401);
  expect((await post('/bot-workflow-permissions/check', permission)).status).toBe(401);
  expect((await post('/runs', { ...insert, status: 'failed' }, signed(insert))).status).toBe(401);
  expect(raw.prepare('SELECT COUNT(*) AS count FROM flow_runs').get()).toEqual({ count: 0 });
  expect((await post('/runs', insert, signed(insert))).status).toBe(201);
  const read = await fetch(base + '/runs/engine-parent/fanouts/group', { headers: signed() });
  expect(read.status).toBe(200); expect(await read.json()).toMatchObject({ success: true, data: { parentStatus: 'running' } });
  const check = await post('/bot-workflow-permissions/check', permission, signed(permission));
  expect(check.status).toBe(200); expect(await check.json()).toEqual({ allowed: true, hasRecords: true });
});
it('does not fall back to unsigned dev mode when the configured public key is invalid', async () => {
  await start('dev', 'not-an-ed25519-key');
  expect((await post('/runs', insert)).status).toBe(503);
  expect((await post('/bot-workflow-permissions/check', permission)).status).toBe(503);
  expect(raw.prepare('SELECT COUNT(*) AS count FROM flow_runs').get()).toEqual({ count: 0 });
});
