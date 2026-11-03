// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { get, type Server } from 'node:http';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import type { ClawWebLifecycle } from '../bootstrap.js';
const state = vi.hoisted(() => ({ db: null as IDatabase | null }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: vi.fn(),
}));
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
let gitDir: string | undefined;
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  await state.db?.close(); state.db = null;
  if (gitDir) await rm(gitDir, { recursive: true, force: true }); gitDir = undefined;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});
it('authorizes Pack snapshots while refusing retired journal recovery', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw); state.db = db;
  await runMigrations(db, 'sqlite');
  await db.exec(`INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, owner_id, gmt_create, gmt_modified)
    VALUES ('wf', 'pack', '{}', 'wf', 'dev_local', 1, 1)`);
  await db.exec(`INSERT INTO bot_workflow_permissions (bot_owner_id, workflow_id, can_view, can_execute, can_edit, gmt_create, gmt_modified)
    VALUES ('dev_local', 'wf', 1, 1, 1, 1, 1)`);
  gitDir = await mkdtemp(path.join(tmpdir(), 'clawweb-public-pack-'));
  execFileSync('git', ['init', '--bare', gitDir], { stdio: 'ignore' });
  vi.stubEnv('AUTO_HEAL_CLAWFLOWS_REMOTE_URL', gitDir);
  vi.stubEnv('PORT', '0'); let server: Server | undefined; const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment: 'dev', profile: 'public', config: { auth: { admins: [] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  const statusUrl = `${base}/api/workflows/wf/pack-operations/request-0001`;
  const read = await fetch(statusUrl);
  expect(read.status).toBe(503); expect(await read.json()).toMatchObject({ code: 'OPERATION_RECOVERY_UNAVAILABLE' });
  expect((await fetch(`${base}/api/workflows/wf/pack-version?version=draft`, { headers: { cookie: 'staff_id=another-user' } })).status).toBe(401);
  expect((await fetch(statusUrl, { headers: { cookie: 'staff_id=another-user' } })).status).toBe(401);
  expect((await fetch(`${statusUrl}/reconcile`, { method: 'POST' })).status).toBe(403);
  expect((await fetch(`${statusUrl}/reconcile`, { method: 'POST', headers: { 'X-Pack-Version-CSRF': '1', origin: 'https://evil.example' } })).status).toBe(403);
  const recover = await fetch(`${statusUrl}/reconcile`, { method: 'POST', headers: { 'X-Pack-Version-CSRF': '1', origin: base } });
  expect(recover.status).toBe(503); expect(await recover.json()).toMatchObject({ code: 'OPERATION_RECOVERY_UNAVAILABLE' });
  // The public runner's explicitly enabled loopback developer can still reach
  // the legacy spec-save path against an isolated local bare repository.
  const localSave = await fetch(`${base}/api/auto-heal/save`, { method: 'POST',
    headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', origin: base },
    body: JSON.stringify({ workflowId: 'local-flow', createOnly: true, requestId: 'local-save-01', spec: {
      id: 'local-flow', version: '1.0.0', title: 'Local', nodes: [
        { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
        { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
      ],
    } }) });
  expect(localSave.status).toBe(200);
  const saved = await localSave.json() as { commit: string; requestId: string };
  const snapshotUrl = `${base}/api/workflows/local-flow/pack-version?version=draft`;
  const snapshotResponse = await fetch(snapshotUrl);
  expect(snapshotResponse.status).toBe(200);
  const snapshot = await snapshotResponse.json() as { baseCommit: string; canWrite: boolean; versionFiles: Array<{ path: string }> };
  expect(snapshot.baseCommit).toBe(saved.commit);
  expect(snapshot.canWrite).toBe(false);
  expect(snapshot.versionFiles.some(file => file.path === 'workflows/local-flow.yaml')).toBe(true);
  expect(execFileSync('git', ['--git-dir', gitDir!, 'rev-parse', 'refs/heads/saved/local-flow'], { encoding: 'utf8' }).trim()).toBe(saved.commit);
  expect((await fetch(snapshotUrl, { headers: { origin: 'https://evil.example' } })).status).toBe(403);
  expect((await fetch(snapshotUrl, { headers: { 'sec-fetch-site': 'cross-site' } })).status).toBe(403);
  expect((await fetch(`${base}/api/workflows/local-flow/PACK-VERSION/?version=draft`, { headers: { origin: 'https://evil.example' } })).status).toBe(403);
  const hostileHostStatus = await new Promise<number | undefined>((resolve, reject) => {
    get(snapshotUrl, { headers: { host: 'evil.example' } }, response => { response.resume(); resolve(response.statusCode); }).on('error', reject);
  });
  expect(hostileHostStatus).toBe(401);
  expect((await fetch(snapshotUrl, { headers: { cookie: 'staff_id=another-user' } })).status).toBe(401);
  expect((await fetch(snapshotUrl, { method: 'PUT', headers: { 'x-pack-version-csrf': '1' } })).status).toBe(503);
  await db.exec("DELETE FROM bot_workflow_permissions WHERE workflow_id = 'local-flow'");
  expect((await fetch(snapshotUrl)).status).toBe(403);
  expect(await db.query("SELECT owner_id FROM workflow_specs WHERE workflow_id = 'local-flow'")).toEqual([{ owner_id: 'dev_local' }]);
  await lifecycle?.stop?.(); lifecycle = undefined; server = undefined;
  lifecycle = await createClawWebBootstrap().start({ environment: 'production', profile: 'public', config: { auth: { admins: [] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const productionBase = `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  expect((await fetch(`${productionBase}/api/workflows/wf/pack-operations/request-0001`, { headers: { 'x-user-id': 'dev_local', cookie: 'staff_id=dev_local' } })).status).toBe(401);
  expect((await fetch(`${productionBase}/api/workflows/local-flow/pack-version?version=draft`, { headers: { cookie: 'staff_id=dev_local' } })).status).toBe(401);
  const request = { method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', 'x-user-id': 'dev_local' },
    body: JSON.stringify({ workflowId: 'new-flow', spec: { id: 'new-flow' }, createOnly: true }) };
  expect((await fetch(`${productionBase}/api/auto-heal/save`, request)).status).toBe(401);
  expect(await db.query("SELECT id FROM workflow_specs WHERE workflow_id = 'new-flow'")).toEqual([]);
}, 20_000);
