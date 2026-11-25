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
// No engines, providers or external schedulers: the public composition, run routes,
// permission resolver and migrated in-memory database remain real.
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

let lifecycle: ClawWebLifecycle | void;
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  if (state.db) await state.db.close(); state.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});
it('wires durable fanout reads and owner-preserving parent/child guards in the actual public host', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw); state.db = db;
  await runMigrations(db, 'sqlite');
  const group = { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent', groupNodeId: 'group',
    groupExecutionId: 'execution', revision: 4, status: 'running', childWorkflow: { id: 'child-workflow' },
    config: { concurrency: 2, successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all' },
    children: ['visible-child', 'private-child'].map((childFlowId, itemIndex) => ({
      parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'execution', itemIndex, itemId: childFlowId,
      attemptNumber: 1, childFlowId, nativeFlowId: childFlowId, status: 'running', result: null, error: null,
    })) };
  raw.exec(`INSERT INTO flow_runs (flow_id, workflow_id, origin_bot_id, status, started_at) VALUES
    ('parent','parent-workflow','bot:reader','running',1),
    ('visible-child','child-workflow','bot:reader','running',2),
    ('private-child','child-workflow','bot:other','running',3);
    INSERT INTO bot_workflow_permissions (bot_id,bot_owner_id,workflow_id,env,can_view,can_execute,can_edit) VALUES
    (NULL,'reader','parent-workflow','dev',1,0,0),('bot','reader','child-workflow','dev',1,0,0);`);
  for (const child of group.children) {
    const fanoutOrigin = { parentFlowId: 'parent', parentNodeId: 'group', groupExecutionId: 'execution',
      itemIndex: child.itemIndex, itemId: child.itemId, attemptNumber: 1, childFlowId: child.childFlowId,
      workflowId: 'child-workflow', requestDigest: 'fixture-digest' };
    raw.prepare('UPDATE flow_runs SET state_json=? WHERE flow_id=?').run(JSON.stringify({ fanoutOrigin }), child.childFlowId);
  }
  raw.prepare('UPDATE flow_runs SET state_json=?, result_json=? WHERE flow_id=?')
    .run(JSON.stringify({ subworkflowFanouts: { group } }), 'private-child-output', 'parent');
  vi.stubEnv('PORT', '0');
  let server: Server | undefined;
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment: 'dev', profile: 'public', config: { auth: { admins: ['admin'] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}/api/runs`;
  const path = '/parent/nodes/group/subworkflows?groupExecutionId=execution';
  const read = (suffix: string, user = 'reader') => fetch(base + suffix, { headers: { 'X-User-Id': user } });
  expect((await fetch(base + path + '&botOwnerId=reader')).status).toBe(401);
  const response = await read(path); expect(response.status).toBe(200);
  expect(response.headers.get('cache-control')).toBe('private, no-store');
  const body = await response.json();
  expect(body).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 1, counts: { pendingCount: 1 },
    children: [{ childFlowId: 'visible-child' }] });
  expect(JSON.stringify(body)).not.toContain('private-child');
  expect((await read('/private-child?botOwnerId=other')).status).toBe(403);
  expect((await read('/private-child/nodes')).status).toBe(403);
  const parent = await (await read('/parent')).json();
  expect(parent.run).toMatchObject({ state_json: null, result_json: null,
    subworkflowGroups: [{ nodeId: 'group', groupExecutionId: 'execution' }] });
  expect(JSON.stringify(parent)).not.toContain('private-child');
  expect(await (await read('/?workflowId=child-workflow&botOwnerId=other')).json()).toMatchObject({ total: 1 });
  expect(await (await read(path, 'admin')).json()).toMatchObject({ countScope: 'full', groupTotal: 2 });
});
