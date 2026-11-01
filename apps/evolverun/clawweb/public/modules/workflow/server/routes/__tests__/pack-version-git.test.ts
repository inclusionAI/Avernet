// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { stringify } from 'yaml';
import { afterEach, expect, it, vi } from 'vitest';
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { packOperationDdl } from '@avernet/clawweb-shared/server/schema-pack-operations';
import { createAutoHealConfigTestDb } from '../../config/__tests__/auto-heal-git-fixtures.js';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import { AutoHealDeployService } from '../../services/auto-heal-deploy-service.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { createLocalRemote, runGit } from '../../services/__tests__/clawflows-git-client-fixture.js';
import { createPackVersionRouter } from '../pack-version.js';
import { readPackVersionFromRemote, savePackVersionToRemote, readPackOperationFromRemote,
  reconcilePackOperationFromRemote } from '../../services/pack-version-remote.js';

const injection = vi.hoisted(() => ({ losePushConfirmation: false, pushes: 0 }));
vi.mock('@avernet/clawweb-shared/server/services/git-execution', async importOriginal => {
  const actual = await importOriginal<typeof import('@avernet/clawweb-shared/server/services/git-execution')>();
  return { ...actual, executeGit: async (...params: Parameters<typeof actual.executeGit>) => {
    const result = await actual.executeGit(...params);
    if (params[0][0] === 'push') {
      injection.pushes++;
      if (injection.losePushConfirmation) throw new Error('lost confirmation: secret remote URL');
    }
    return result;
  } };
});
const spec = { id: 'flow', title: 'Route fixture', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
let root = '', db: SqliteDatabase | undefined;
let server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(e => e ? reject(e) : resolve()));
  server = undefined;
  if (db) await db.close(); db = undefined;
  vi.restoreAllMocks(); injection.losePushConfirmation = false; injection.pushes = 0;
  if (root) rmSync(root, { recursive: true, force: true });
});

async function fixture() {
  root = mkdtempSync(path.join(tmpdir(), 'version-route-'));
  const bare = createLocalRemote(root), seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), stringify(spec));
  writeFileSync(path.join(seed, 'packs/pack/asset.bin'), Buffer.from([0, 255]));
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'pack']);
  const sha = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${sha}:refs/heads/wf/flow`]);
  db = createAutoHealConfigTestDb();
  await db.exec(`
    CREATE TABLE workflow_specs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT,
      title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER
    );
    CREATE TABLE workflow_deploy_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER,
      version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT,
      note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER
    );
  `);
  for (const ddl of packOperationDdl) await db.exec(ddl);
  const workflowSpecRepo = new WorkflowSpecRepository(db);
  await workflowSpecRepo.upsert('flow', 'pack', JSON.stringify(spec), 'trusted');
  await new AppConfigRepository(db).create({ config_key: 'git', config_yaml: stringify({ remoteUrl: bare }) });
  const query = vi.spyOn(db, 'query');
  const ctx = { db, gitConfig: { remoteUrl: '/invalid-fallback', username: 'fixture', email: 'fixture@example.invalid', token: '' },
    gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' } };
  const principal = { actorId: 'trusted' };
  const app = express(); app.use(express.json()); app.use(createPackVersionRouter({
    resolvePrincipal: async () => principal, botPermRepo: { hasEditPermission: async () => true }, workflowSpecRepo,
    readSnapshot: (workflowId, packId, version) => readPackVersionFromRemote({ ...ctx, workflowId, packId, version }),
    saveCandidate: input => savePackVersionToRemote({ ...ctx, ...input }),
    readOperation: (requestId, workflowId, actorId) => readPackOperationFromRemote({ ...ctx, requestId, workflowId, actorId }),
    reconcileOperation: (requestId, workflowId, actorId) => reconcilePackOperationFromRemote({ ...ctx, requestId, workflowId, actorId }),
  }));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('address');
  const base = `http://127.0.0.1:${address.port}/flow`;
  const body = { sourceVersion: 'current', sourceCommit: sha, expectedSavedHead: null,
    changes: [{ path: 'asset.bin', operation: 'replace', contentBase64: 'AAE=', mode: '100755' }] };
  const remoteGit = (...args: string[]) => runGit(root, ['--git-dir', bare, ...args]);
  const save = (requestId: string, contentBase64 = 'AAE=') => fetch(`${base}/pack-version`, {
    method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...body, requestId, changes: [{ ...body.changes[0], contentBase64 }] }),
  });
  const history = new WorkflowDeployHistoryRepository(db);
  const deploy = new AutoHealDeployService(db, workflowSpecRepo, history,
    { hasEditPermission: async () => true, hasExecutePermission: async () => true } as never,
    { ...ctx.gitConfig, remoteUrl: bare }, ctx.gitAuthor);
  return { db, query, base, sha, remoteGit, save, principal, workflowSpecRepo, body, deploy };
}

it('reads and saves a complete binary Git pack using a real remote with lease conflicts', async () => {
  const f = await fixture();
  const response = await fetch(`${f.base}/pack-version?version=current`);
  expect(response.status).toBe(200); const snapshot = await response.json();
  expect(snapshot).toMatchObject({ baseCommit: f.sha, draftHead: null, canWrite: true });
  expect(snapshot.versionFiles).toContainEqual({ path: 'asset.bin', contentBase64: 'AP8=', mode: '100644' });
  const results = await Promise.all([f.save('save-race-first', 'AAE='), f.save('save-race-second', 'AAI=')]);
  expect(results.filter(r => r.status === 200)).toHaveLength(1);
  const failure = results.find(r => r.status !== 200)!;
  expect(failure.status).toBe(409);
  const winner = await results.find(r => r.status === 200)!.json(), failed = await failure.json();
  // The losing request is fenced by the journal, or observes the winner's moved saved ref.
  expect(['WORKFLOW_BUSY', 'VERSION_CONFLICT']).toContain(failed.code);
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(winner.commit);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
  expect(f.remoteGit('tag', '--list')).toBe('');
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  expect(await f.db.query("SELECT actor_id, phase FROM clawweb_pack_operations WHERE phase = 'completed'"))
    .toEqual([{ actor_id: 'trusted', phase: 'completed' }]);
  expect(injection.pushes).toBe(1);
  const draft = await fetch(`${f.base}/pack-version?version=draft`);
  expect(draft.status).toBe(200); const changed = await draft.json();
  expect(changed.versionFiles).toContainEqual({ path: 'asset.bin', contentBase64: results[0].status === 200 ? 'AAE=' : 'AAI=', mode: '100755' });
  const configs = f.query.mock.calls.filter(([sql]) => sql.includes('cm_app_config'));
  expect(configs).toHaveLength(4);
  expect(configs.every(([, params]) => JSON.stringify(params) === '["git"]')).toBe(true);
}, 30000);

it('returns unknown over HTTP after a real successful push loses its confirmation and reconciles the same candidate', async () => {
  const f = await fixture(), requestId = 'save-lost-confirmation';
  const save = () => fetch(`${f.base}/pack-version`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...f.body, requestId, changes: [
      { path: 'new.txt', operation: 'create', contentBase64: Buffer.from('saved').toString('base64'), mode: '100644' },
    ] }),
  });
  injection.losePushConfirmation = true;
  const response = await save();
  expect(response.status).toBe(503); expect(response.headers.get('cache-control')).toBe('no-store');
  const result = await response.json();
  expect(result).toEqual({ error: 'Workflow operation could not be completed', code: 'SAVE_OUTCOME_UNKNOWN', requestId,
    branch: 'saved/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
  expect(JSON.stringify(result)).not.toContain('secret'); expect(injection.pushes).toBe(1);
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(result.commit);
  expect(f.remoteGit('show', `${result.commit}:packs/pack/new.txt`)).toBe('saved');
  expect((await f.workflowSpecRepo.findByWorkflowId('flow'))?.spec_json).toBe(JSON.stringify(spec));
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  const status = await fetch(`${f.base}/pack-operations/${requestId}`), receipt = await status.json();
  expect(status.status).toBe(200); expect(receipt).toEqual({ requestId, kind: 'save', state: 'unknown' });
  expect(JSON.stringify(receipt)).not.toMatch(/bundleBase64|baseline|specJson|gitConfig/);
  expect(injection.pushes).toBe(1);
  expect((await save()).status).toBe(409); expect(injection.pushes).toBe(1);
  injection.losePushConfirmation = false;
  const recovery = await fetch(`${f.base}/pack-operations/${requestId}/reconcile`, { method: 'POST' });
  expect(recovery.status).toBe(200);
  expect(await recovery.json()).toMatchObject({ state: 'completed', result: { commit: result.commit, requestId } });
  expect(injection.pushes).toBe(1);
  const replay = await save(); expect(replay.status).toBe(200);
  expect(await replay.json()).toMatchObject({ saved: true, requestId, commit: result.commit });
  expect(injection.pushes).toBe(1); expect(f.remoteGit('tag', '--list')).toBe('');
}, 30000);

it('reports DB projection failure as unknown after Git success without formal history or a false success response', async () => {
  const f = await fixture(), requestId = 'save-db-write-failed';
  await f.db.exec('CREATE TRIGGER ignore_projection BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END');
  const response = await f.save(requestId), result = await response.json();
  expect(response.status).toBe(503); expect(result).toMatchObject({ code: 'SAVE_OUTCOME_UNKNOWN', requestId });
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(result.commit);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
  expect((await f.workflowSpecRepo.findByWorkflowId('flow'))?.spec_json).toBe(JSON.stringify(spec));
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  expect(await f.db.query('SELECT phase, lock_workflow_id FROM clawweb_pack_operations')).toEqual([{ phase: 'unknown', lock_workflow_id: (await f.workflowSpecRepo.findByWorkflowId('flow'))!.id }]);
  await f.db.exec('DROP TRIGGER ignore_projection');
  const recovered = await fetch(`${f.base}/pack-operations/${requestId}/reconcile`, { method: 'POST' });
  expect(recovered.status).toBe(200); expect((await recovered.json()).state).toBe('completed');
  expect(injection.pushes).toBe(1);
  expect(JSON.parse((await f.workflowSpecRepo.findByWorkflowId('flow'))!.spec_json).content).toBe(stringify(spec));
}, 30000);

it('does not expose or reconcile another actors private journal even when that actor can edit', async () => {
  const f = await fixture(), requestId = 'save-private-operation';
  injection.losePushConfirmation = true;
  expect((await f.save(requestId)).status).toBe(503);
  f.principal.actorId = 'another-editor';
  const url = `${f.base}/pack-operations/${requestId}`;
  for (const response of [await fetch(url), await fetch(`${url}/reconcile`, { method: 'POST' })]) {
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ code: 'OPERATION_NOT_FOUND' });
  }
  expect(injection.pushes).toBe(1);
  expect(await f.db.query('SELECT phase FROM clawweb_pack_operations')).toEqual([{ phase: 'unknown' }]);
}, 30000);

it('saves current release R over observed saved S with the same tree, without republishing', async () => {
  const f = await fixture();
  const savedResponse = await f.save('save-before-release'); expect(savedResponse.status).toBe(200);
  const saved = await savedResponse.json();
  const release = await f.deploy.deploy({ workflowId: 'flow', actorId: 'trusted', candidateCommit: saved.commit, autoRun: false });
  expect(release.commit).not.toBe(saved.commit);
  expect(f.remoteGit('rev-parse', `${release.commit}^{tree}`)).toBe(f.remoteGit('rev-parse', `${saved.commit}^{tree}`));
  const current = await fetch(`${f.base}/pack-version?version=current`);
  expect(current.status).toBe(200);
  expect(await current.json()).toMatchObject({ baseCommit: release.commit, draftHead: saved.commit });
  const saveCurrent = (requestId: string) => fetch(`${f.base}/pack-version`, { method: 'PUT',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...f.body, requestId,
      sourceCommit: release.commit, expectedSavedHead: saved.commit }),
  });
  const edited = await saveCurrent('save-current-after-release'); expect(edited.status).toBe(200);
  const result = await edited.json();
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(result.commit);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(release.commit);
  expect(f.remoteGit('tag', '--list')).toBe('deploy/flow/#1');
  expect(await f.db.query('SELECT action, is_active FROM workflow_deploy_history')).toEqual([{ action: 'deploy', is_active: 1 }]);
  const stale = await saveCurrent('save-stale-observed-head');
  expect(stale.status).toBe(409); expect((await stale.json()).code).toBe('VERSION_CONFLICT');
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(result.commit);
}, 30000);

it('rejects a draft source that differs from its observed head but saves a matching draft', async () => {
  const f = await fixture(); const first = await f.save('save-draft-base'); expect(first.status).toBe(200);
  const saved = await first.json();
  const saveDraft = (requestId: string, sourceCommit: string) => fetch(`${f.base}/pack-version`, { method: 'PUT',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...f.body, requestId,
      sourceVersion: 'draft', sourceCommit, expectedSavedHead: saved.commit }),
  });
  const mismatch = await saveDraft('save-draft-mismatch', f.sha);
  expect(mismatch.status).toBe(409); expect((await mismatch.json()).code).toBe('VERSION_CONFLICT');
  expect(injection.pushes).toBe(1);
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(saved.commit);
  const matched = await saveDraft('save-draft-matched', saved.commit); expect(matched.status).toBe(200);
  expect(f.remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe((await matched.json()).commit);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
}, 30000);
