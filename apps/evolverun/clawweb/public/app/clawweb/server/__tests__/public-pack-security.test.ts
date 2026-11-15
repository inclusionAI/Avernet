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
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
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
  const firstDraftPayload = JSON.stringify({ workflowId: 'wf', spec: { id: 'wf' },
    expectedDraftAbsent: true, expectedActiveDeployNumber: 1, expectedActiveToken: 'a'.repeat(64) });
  const beforeRejectedWrites = await db.query("SELECT * FROM workflow_specs WHERE workflow_id = 'wf'");
  for (const endpoint of ['deploy', 'editor-deploy', 'editor-save-first-draft', 'unified-save']) {
    const url = `${base}/api/auto-heal/${endpoint}`;
    for (const headers of [{}, { 'x-pack-version-csrf': '1', origin: 'https://evil.example' },
      { 'x-pack-version-csrf': '1', 'sec-fetch-site': 'cross-site' }]) {
      const rejected = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: firstDraftPayload });
      expect(rejected.status).toBe(403);
      expect(await rejected.json()).toMatchObject({ code: 'CSRF_REJECTED' });
    }
    const allowed = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', origin: base }, body: '{}' });
    expect(allowed.status).toBe(400);
  }
  expect(await db.query("SELECT * FROM workflow_specs WHERE workflow_id = 'wf'")).toEqual(beforeRejectedWrites);
  // Editor draft CAS has the same host trust and CSRF boundary as Pack writes.
  const draftUrl = `${base}/api/workflows/wf/editor-draft`;
  expect((await fetch(draftUrl, { headers: { cookie: 'staff_id=another-user' } })).status).toBe(401);
  expect((await fetch(draftUrl, { method: 'PUT' })).status).toBe(403);
  expect((await fetch(draftUrl, { method: 'PUT', headers: { 'x-pack-version-csrf': '1', origin: 'https://evil.example' } })).status).toBe(403);
  const draft = await fetch(draftUrl);
  expect(draft.status).toBe(200); expect(draft.headers.get('cache-control')).toBe('no-store');
  const draftBody = await draft.json() as { draftToken: string };
  expect(draftBody).toMatchObject({ workflowId: 'wf', packId: 'pack', specJson: '{}', draftToken: expect.stringMatching(/^[a-f0-9]{64}$/) });
  const updatedSpec = { id: 'wf', version: '1.0.0', title: 'CAS draft', nodes: [
    { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
    { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
  ] };
  const putDraft = () => fetch(draftUrl, { method: 'PUT', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', origin: base },
    body: JSON.stringify({ expectedDraftToken: draftBody.draftToken, spec: updatedSpec }) });
  const updated = await putDraft(); expect(updated.status).toBe(200);
  expect(JSON.parse((await updated.json() as { specJson: string }).specJson)).toEqual(updatedSpec);
  const stale = await putDraft(); expect(stale.status).toBe(409); expect(await stale.json()).toMatchObject({ code: 'DRAFT_CONFLICT' });
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
  expect(execFileSync('git', ['--git-dir', gitDir!, 'rev-parse', 'refs/heads/wf/local-flow'], { encoding: 'utf8' }).trim()).toBe(saved.commit);
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
  expect((await fetch(`${productionBase}/api/workflows/wf/editor-draft`, { headers: { cookie: 'staff_id=dev_local' } })).status).toBe(401);
  expect((await fetch(`${productionBase}/api/workflows/wf/unified-save`, { method: 'POST', headers: { 'x-pack-version-csrf': '1', 'x-user-id': 'dev_local', cookie: 'staff_id=dev_local' } })).status).toBe(401);
  const request = { method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', 'x-user-id': 'dev_local' },
    body: JSON.stringify({ workflowId: 'new-flow', spec: { id: 'new-flow' }, createOnly: true }) };
  expect((await fetch(`${productionBase}/api/auto-heal/save`, request)).status).toBe(401);
  for (const endpoint of ['deploy', 'editor-deploy']) {
    expect((await fetch(`${productionBase}/api/auto-heal/${endpoint}`, request)).status).toBe(401);
  }
  const firstDraftRequest = { ...request, body: JSON.stringify({ workflowId: 'new-flow', spec: { id: 'new-flow' },
    expectedDraftAbsent: true, expectedActiveDeployNumber: 1, expectedActiveToken: 'a'.repeat(64) }) };
  expect((await fetch(`${productionBase}/api/auto-heal/editor-save-first-draft`, firstDraftRequest)).status).toBe(401);
  expect(await db.query("SELECT id FROM workflow_specs WHERE workflow_id = 'new-flow'")).toEqual([]);
}, 20_000);


it('never derives unified-save identity from unverified public cookies or headers', async () => {
  state.db = new SqliteDatabase(new Database(':memory:')); await runMigrations(state.db, 'sqlite');
  vi.stubEnv('PORT', '0'); let server: Server | undefined; const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment: 'dev', profile: 'public', config: { auth: { admins: [] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  const forgedJwt = `e30.${Buffer.from(JSON.stringify({ sno: 'dev_local' })).toString('base64url')}.forged`;
  for (const cookie of ['', 'staff_id=dev_local', ['IAM_TOKEN', forgedJwt].join('=')]) {
    const response = await fetch(`${base}/api/workflows/wf/unified-save`, { method: 'POST', headers: {
      'content-type': 'application/json', 'x-pack-version-csrf': '1', origin: base, 'x-user-id': 'dev_local', cookie,
    }, body: '{}' });
    expect(response.status).toBe(401); expect(await response.json()).toEqual({ code: 'AUTHENTICATION_REQUIRED' });
  }
}, 20_000);


it('uses edit and an optional host policy for snapshot v2 while preserving non-v2 policy and disabling file writes', async () => {
  const db = new SqliteDatabase(new Database(':memory:')); state.db = db; await runMigrations(db, 'sqlite');
  gitDir = await mkdtemp(path.join(tmpdir(), 'clawweb-editor-acl-'));
  execFileSync('git', ['init', '--bare', gitDir], { stdio: 'ignore' });
  vi.stubEnv('AUTO_HEAL_CLAWFLOWS_REMOTE_URL', gitDir); vi.stubEnv('GIT_ALLOW_PROTOCOL', 'file'); vi.stubEnv('PORT', '0');
  let server: Server | undefined; const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) { server = listen.apply(this, args); return server; });
  const start = async (authorizeSnapshotDeploy?: (candidate: { actorId: string; workflowId: string; packId: string; candidateCommit: string }) => Promise<boolean>) => {
    lifecycle = await createClawWebBootstrap({ authorizeSnapshotDeploy }).start({ environment: 'dev', profile: 'public', config: { auth: { admins: [] } }, configSources: [] });
    if (!server!.listening) await once(server!, 'listening');
    return `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  };
  let base = await start();
  const spec = { id: 'acl-flow', version: '1.0.0', title: 'ACL', nodes: [
    { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
    { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
  ] };
  const headers = () => ({ 'content-type': 'application/json', 'x-pack-version-csrf': '1', origin: base });
  const created = await fetch(`${base}/api/auto-heal/save`, { method: 'POST', headers: headers(), body: JSON.stringify({ workflowId: spec.id, createOnly: true, requestId: 'acl-save-0001', spec }) });
  expect(created.status).toBe(200); const saved = await created.json() as { commit: string; packId: string };
  const draftResponse = await fetch(`${base}/api/workflows/${spec.id}/editor-draft`);
  expect(draftResponse.status).toBe(200); const draft = await draftResponse.json() as { draftToken: string };
  const payload = { workflowId: spec.id, packId: saved.packId, candidateCommit: saved.commit, expectedDraftToken: draft.draftToken, requestId: 'acl-deploy-001', autoRun: false };
  const post = (suffix: string, body: object, extra: Record<string, string> = {}) => fetch(`${base}/api/auto-heal/${suffix}`, { method: 'POST', headers: { ...headers(), ...extra }, body: JSON.stringify(body) });
  const exact = { ...payload, snapshotProtocol: 'saved-tree-v2', confirmedFileDiff: true, expectedActiveCommit: '' };
  const missingPolicy = await post('editor-deploy', payload);
  expect(missingPolicy.status).toBe(403); expect(await missingPolicy.json()).toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
  expect((await fetch(`${base}/api/auto-heal/editor-deploy/snapshot-v2/state?workflowId=${spec.id}`)).status).toBe(200);
  await lifecycle?.stop?.(); lifecycle = undefined; server = undefined;
  let allow = false;
  const acl = vi.fn(async (candidate: { actorId: string; workflowId: string; packId: string; candidateCommit: string }) => {
    expect(candidate).toMatchObject({ actorId: 'dev_local', workflowId: spec.id, packId: saved.packId });
    expect(candidate.candidateCommit).toMatch(/^[a-f0-9]{40,64}$/); return allow;
  });
  base = await start(acl);
  const forged = await post('editor-deploy/snapshot-v2', exact, { cookie: 'staff_id=other', 'x-user-id': 'dev_local' });
  expect(forged.status).toBe(401); expect(acl).not.toHaveBeenCalled();
  await db.exec("UPDATE bot_workflow_permissions SET can_view = 1, can_execute = 1, can_edit = 0 WHERE workflow_id = 'acl-flow' AND bot_owner_id = 'dev_local'");
  for (const [suffix, body] of [['editor-deploy', payload], ['editor-deploy/snapshot-v2', exact]] as const) {
    const denied = await post(suffix, body); expect(denied.status).toBe(403); expect(await denied.json()).toMatchObject({ code: 'FORBIDDEN' });
  }
  expect(acl).not.toHaveBeenCalled();
  await db.exec("UPDATE bot_workflow_permissions SET can_edit = 1 WHERE workflow_id = 'acl-flow' AND bot_owner_id = 'dev_local'");
  const deniedPolicy = await post('editor-deploy', payload);
  expect(deniedPolicy.status).toBe(403); expect(await deniedPolicy.json()).toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
  const deniedState = await fetch(`${base}/api/auto-heal/editor-deploy/snapshot-v2/state?workflowId=${spec.id}`);
  expect(deniedState.status).toBe(403); expect(await deniedState.json()).toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
  const deniedSnapshot = await post('editor-deploy/snapshot-v2', exact);
  expect(deniedSnapshot.status).toBe(403); expect(await deniedSnapshot.json()).toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
  expect(await db.query("SELECT * FROM workflow_deploy_history WHERE workflow_id = 'acl-flow'")).toEqual([]);
  allow = true;
  acl.mockClear();
  const snapshotUrl = `${base}/api/workflows/${spec.id}/pack-version?version=draft`;
  const snapshot = await fetch(snapshotUrl); expect(snapshot.status).toBe(200); expect(await snapshot.json()).toMatchObject({ canWrite: false, baseCommit: saved.commit });
  expect((await fetch(snapshotUrl, { method: 'PUT', headers: headers(), body: '{}' })).status).toBe(503);
  const capability = await fetch(`${base}/api/auto-heal/editor-deploy/snapshot-v2/state?workflowId=${spec.id}`);
  expect(capability.status).toBe(200); expect(await capability.json()).toMatchObject({ canDeploy: true, savedCommit: saved.commit, packId: saved.packId, activeCommit: null });
  const result = await post('editor-deploy/snapshot-v2', exact);
  expect(result.status).toBe(200); expect(await result.json()).toMatchObject({ ok: true, deployed: true, candidateCommit: saved.commit });
  expect(execFileSync('git', ['--git-dir', gitDir!, 'rev-parse', 'refs/heads/wf/acl-flow'], { encoding: 'utf8' }).trim()).toBe(saved.commit);
  expect(execFileSync('git', ['--git-dir', gitDir!, 'rev-parse', 'refs/tags/deploy/acl-flow/#1^{commit}'], { encoding: 'utf8' }).trim()).toBe(saved.commit);
  expect(acl).toHaveBeenCalledTimes(3);
  expect(acl).toHaveBeenCalledWith({ actorId: 'dev_local', workflowId: spec.id, packId: saved.packId, candidateCommit: saved.commit });
  expect(await db.query("SELECT is_active FROM workflow_deploy_history WHERE workflow_id = 'acl-flow'")).toEqual([{ is_active: 1 }]);
}, 45_000);
