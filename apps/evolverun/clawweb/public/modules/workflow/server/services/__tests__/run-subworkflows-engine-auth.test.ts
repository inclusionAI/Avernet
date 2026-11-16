// @vitest-environment node
import { generateKeyPairSync, sign } from 'node:crypto';
import { once } from 'node:events';
import type { Server } from 'node:http';
import Database from 'better-sqlite3';
import express from 'express';
import { afterEach, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import type { ClawWebLifecycle } from '@avernet/clawweb/server/bootstrap';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';

const host = vi.hoisted(() => ({ db: null as IDatabase | null, key: '' }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: host.db }), closeDatabase: vi.fn(),
  resolveSignatureConfig: () => ({ publicKeyB64: host.key }),
}));
// Keep the real bootstrap, engineAuth, run router, SQLite, and reader.
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }),
    createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('@avernet/clawweb/server/repair-ais-execution', () => ({ resolveRepairAisConfiguration: async () => null }));
import { createClawWebBootstrap } from '@avernet/clawweb/server/public-bootstrap';

let lifecycle: ClawWebLifecycle | void;
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  if (host.db) await host.db.close(); host.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it('enforces the configured engine signature at the actual public bootstrap before ordinary run writes', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw); host.db = db;
  await runMigrations(db, 'sqlite');
  const keys = generateKeyPairSync('ed25519');
  host.key = keys.publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
  const origin = { parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'generation', itemId: 'item',
    itemIndex: 0, attemptNumber: 1, childFlowId: 'logical', workflowId: 'child-wf', requestDigest: 'digest' };
  const group = { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent',
    groupNodeId: 'group', groupExecutionId: 'generation', revision: 1, status: 'running',
    config: { concurrency: 1, successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all' },
    childWorkflow: { id: 'child-wf' }, children: [{ ...origin, nativeFlowId: 'native', status: 'running', error: null }] };
  raw.prepare('INSERT INTO flow_runs (flow_id,workflow_id,origin_bot_id,status,started_at,state_json) VALUES (?,?,?,?,?,?)')
    .run('parent', 'parent-wf', 'bot:alice', 'running', 1, JSON.stringify({ subworkflowFanouts: { group } }));
  const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), {
    resolveRunViewScope: async () => ({ bots: [{ botId: 'bot', ownerId: 'alice' }] }),
    listRunViewScopes: async () => new Map(), getViewByIdsForOwner: async () => ({ restrictedIds: new Set(), viewableIds: new Set() }),
    resolveViewScope: async () => 'all' as const,
  });
  const read = () => reader.readGroup({ actorId: 'reader', isAdmin: false }, 'parent', 'group', { groupExecutionId: 'generation' });
  const body = { flow_id: 'native', workflow_id: 'child-wf', status: 'running', origin_bot_id: 'bot:alice',
    state_json: JSON.stringify({ fanoutOrigin: origin, businessStatus: 'RUNNING' }) };
  vi.stubEnv('PORT', '0'); let server: Server | undefined;
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment: 'prod', profile: 'public', config: {}, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}/api/internal/runs`;
  const post = (headers: Record<string, string> = {}) => fetch(base, { method: 'POST',
    headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });
  expect((await post({ 'X-User-Id': 'admin', 'X-Forwarded-For': '127.0.0.1' })).status).toBe(401);
  const update = await fetch(`${base}/parent/state`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ state_json: JSON.stringify({ subworkflowFanouts: { group } }) }) });
  expect(update.status).toBe(401);
  expect(raw.prepare("SELECT flow_id FROM flow_runs WHERE flow_id='native'").get()).toBeUndefined();
  expect((await read()).visibleTotal).toBe(0);
  const timestamp = String(Date.now());
  const signature = sign(null, Buffer.from(`${timestamp}.${JSON.stringify(body)}`), keys.privateKey).toString('base64');
  expect((await post({ 'X-Timestamp': timestamp, 'X-Signature': signature })).status).toBe(201);
  expect((await read()).children).toMatchObject([{ childFlowId: 'native' }]);
});
