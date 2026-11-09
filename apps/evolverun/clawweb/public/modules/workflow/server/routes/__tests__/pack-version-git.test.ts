// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { stringify } from 'yaml';
import { afterEach, expect, it, vi } from 'vitest';
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
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
  if (root) rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
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
  const workflowSpecRepo = new WorkflowSpecRepository(db);
  await workflowSpecRepo.upsert('flow', 'pack', JSON.stringify(spec), 'trusted');
  await new AppConfigRepository(db).create({ config_key: 'git', config_yaml: stringify({ remoteUrl: bare }) });
  const query = vi.spyOn(db, 'query');
  const ctx = { db, gitConfig: { remoteUrl: '/invalid-fallback', username: 'fixture', email: 'fixture@example.invalid', token: '' },
    gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' } };
  const principal = { actorId: 'trusted' };
  const hasEditPermission = vi.fn(async (_workflowId: string, _actorId: string) => true);
  const app = express(); app.use(express.json()); app.use(createPackVersionRouter({
    resolvePrincipal: async () => principal, botPermRepo: { hasEditPermission }, workflowSpecRepo,
    readSnapshot: (workflowId, packId, version) => readPackVersionFromRemote({ ...ctx, workflowId, packId, version }),
    saveCandidate: input => savePackVersionToRemote({ ...ctx, ...input }),
    readOperation: (requestId, workflowId, actorId) => readPackOperationFromRemote({ ...ctx, requestId, workflowId, actorId }),
    reconcileOperation: (requestId, workflowId, actorId) => reconcilePackOperationFromRemote({ ...ctx, requestId, workflowId, actorId }),
  }));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('address');
  const base = `http://127.0.0.1:${address.port}/flow`;
  const body = { sourceVersion: 'draft', sourceCommit: sha, expectedSavedHead: sha,
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
  return { db, query, base, sha, remoteGit, save, principal, hasEditPermission, workflowSpecRepo, body, deploy };
}

it('reads and saves a complete binary Git pack using a real remote with lease conflicts', async () => {
  const f = await fixture();
  const response = await fetch(`${f.base}/pack-version?version=draft`);
  expect(response.status).toBe(200); const snapshot = await response.json();
  expect(snapshot).toMatchObject({ baseCommit: f.sha, draftHead: f.sha, canWrite: true });
  expect(snapshot.versionFiles).toContainEqual({ path: 'asset.bin', contentBase64: 'AP8=', mode: '100644' });
  const results = await Promise.all([f.save('save-race-first', 'AAE='), f.save('save-race-second', 'AAI=')]);
  expect(results.filter(r => r.status === 200)).toHaveLength(1);
  const failure = results.find(r => r.status !== 200)!;
  const winner = await results.find(r => r.status === 200)!.json(), failed = await failure.json();
  // A failed lease push is conservatively unknown; a stale pre-push read is a conflict.
  expect([[409, 'VERSION_CONFLICT'], [503, 'SAVE_OUTCOME_UNKNOWN']]).toContainEqual([failure.status, failed.code]);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(winner.commit);
  expect(f.remoteGit('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remoteGit('tag', '--list')).toBe('');
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  expect(await f.db.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'clawweb_pack_operations'")).toEqual([]);
  expect((await f.workflowSpecRepo.findByWorkflowId('flow'))?.spec_json).toBe(JSON.stringify(spec));
  expect(injection.pushes).toBe(1);
  const draft = await fetch(`${f.base}/pack-version?version=draft`);
  expect(draft.status).toBe(200); const changed = await draft.json();
  expect(changed.versionFiles).toContainEqual({ path: 'asset.bin', contentBase64: results[0].status === 200 ? 'AAE=' : 'AAI=', mode: '100755' });
  const configs = f.query.mock.calls.filter(([sql]) => sql.includes('cm_app_config'));
  expect(configs).toHaveLength(4);
  expect(configs.every(([, params]) => JSON.stringify(params) === '["git"]')).toBe(true);
}, 30000);

it('reports a lost push confirmation as unknown without a journal or a blind retry', async () => {
  const f = await fixture(), requestId = 'save-lost-confirmation';
  const save = () => fetch(`${f.base}/pack-version`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...f.body, requestId, changes: [
      { path: 'new.txt', operation: 'create', contentBase64: Buffer.from('saved').toString('base64'), mode: '100644' },
    ] }),
  });
  injection.losePushConfirmation = true;
  const response = await save(), result = await response.json();
  expect(response.status).toBe(503); expect(response.headers.get('cache-control')).toBe('no-store');
  expect(result).toEqual({ error: 'Save outcome unknown; inspect the Git branch manually before retrying', code: 'SAVE_OUTCOME_UNKNOWN',
    branch: 'wf/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
  expect(JSON.stringify(result)).not.toContain('secret'); expect(injection.pushes).toBe(1);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
  expect(f.remoteGit('show', `${result.commit}:packs/pack/new.txt`)).toBe('saved');
  expect(f.remoteGit('branch', '--list', 'saved/flow')).toBe('');
  expect((await f.workflowSpecRepo.findByWorkflowId('flow'))?.spec_json).toBe(JSON.stringify(spec));
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  expect(await f.db.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'clawweb_pack_operations'")).toEqual([]);
  const url = `${f.base}/pack-operations/${requestId}`;
  for (const status of [await fetch(url), await fetch(`${url}/reconcile`, { method: 'POST' })]) {
    expect(status.status).toBe(503); expect(status.headers.get('cache-control')).toBe('no-store');
    expect(await status.json()).toEqual({ error: 'Workflow operation could not be completed', code: 'OPERATION_RECOVERY_UNAVAILABLE' });
  }
  injection.losePushConfirmation = false;
  const retry = await save();
  expect(retry.status).toBe(409); expect((await retry.json()).code).toBe('VERSION_CONFLICT');
  expect(injection.pushes).toBe(1); expect(f.remoteGit('tag', '--list')).toBe('');
  const draft = await fetch(`${f.base}/pack-version?version=draft`);
  expect(draft.status).toBe(200);
  expect(await draft.json()).toMatchObject({ baseCommit: result.commit, draftHead: result.commit });
  // A request ID is only correlation: a new, explicitly based edit is not a replay receipt.
  const next = await fetch(`${f.base}/pack-version`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...f.body, requestId, sourceVersion: 'draft', sourceCommit: result.commit,
      expectedSavedHead: result.commit, changes: [{ path: 'new.txt', operation: 'replace',
        contentBase64: Buffer.from('next').toString('base64'), mode: '100644' }] }),
  });
  expect(next.status).toBe(200); const nextResult = await next.json();
  expect(nextResult).toMatchObject({ requestId, saved: true });
  expect(nextResult.commit).not.toBe(result.commit);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(nextResult.commit);
  expect(f.remoteGit('show', `${nextResult.commit}:packs/pack/new.txt`)).toBe('next');
  expect(injection.pushes).toBe(2);
}, 30000);

it('saves files without projecting Git YAML into the database and rejects workflow YAML edits', async () => {
  const f = await fixture();
  const before = await f.workflowSpecRepo.findByWorkflowId('flow');
  await f.db.exec("CREATE TRIGGER reject_projection BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'unexpected projection'); END");
  const response = await f.save('save-no-projection');
  expect(response.status).toBe(200); const result = await response.json();
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
  expect(f.remoteGit('show', `${result.commit}:packs/pack/workflows/flow.yaml`)).toBe(stringify(spec).trim());
  expect(await f.workflowSpecRepo.findByWorkflowId('flow')).toEqual(before);
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  const yaml = await fetch(`${f.base}/pack-version`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...f.body, requestId: 'save-yaml-rejected', sourceVersion: 'draft',
      sourceCommit: result.commit, expectedSavedHead: result.commit,
      changes: [{ path: 'workflows/flow.yaml', operation: 'replace', contentBase64: Buffer.from(stringify({ ...spec, title: 'Changed' })).toString('base64'), mode: '100644' }] }),
  });
  expect(yaml.status).toBe(400); expect(await yaml.json()).toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
  expect(injection.pushes).toBe(1);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
  expect(f.remoteGit('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remoteGit('tag', '--list')).toBe('');
  expect(await f.workflowSpecRepo.findByWorkflowId('flow')).toEqual(before);
}, 30000);

it('denies an actor without edit permission before reading or saving Pack files', async () => {
  const f = await fixture();
  f.principal.actorId = 'another-actor'; f.hasEditPermission.mockResolvedValue(false);
  for (const response of [await fetch(`${f.base}/pack-version?version=current`), await f.save('denied-save-request')]) {
    expect(response.status).toBe(403); expect(await response.json()).toMatchObject({ code: 'FORBIDDEN' });
  }
  expect(f.hasEditPermission).toHaveBeenCalledWith('flow', 'another-actor');
  expect(injection.pushes).toBe(0);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
  expect(f.remoteGit('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remoteGit('tag', '--list')).toBe('');
  expect((await f.workflowSpecRepo.findByWorkflowId('flow'))?.spec_json).toBe(JSON.stringify(spec));
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
}, 30000);

it('rejects current release R over a newer wf draft S even with the same tree, without changing the draft', async () => {
  const f = await fixture();
  f.remoteGit('tag', 'deploy/flow/#1', f.sha);
  await new WorkflowDeployHistoryRepository(f.db).insert({ workflowId: 'flow', packId: 'pack', deployNumber: 1, version: 1, tagName: 'deploy/flow/#1', action: 'deploy', specJson: JSON.stringify(spec), isActive: true });
  // Keep every active path and mode, including the unmodified non-Pack README.
  // Only the asset bytes change, so this is a valid deployment candidate.
  const savedResponse = await fetch(`${f.base}/pack-version`, { method: 'PUT',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...f.body,
      requestId: 'save-before-release', changes: [{ ...f.body.changes[0], mode: '100644' }] }),
  });
  expect(savedResponse.status).toBe(200);
  const saved = await savedResponse.json();
  const treeModes = (commit: string) => f.remoteGit('ls-tree', '-r', '-t', '--full-tree', commit)
    .split('\n').map(line => line.replace(/^(\d{6} \w+) [a-f0-9]+\t/, '$1\t'));
  expect(treeModes(f.sha)).toEqual([
    '100644 blob\tREADME', '040000 tree\tpacks', '040000 tree\tpacks/pack',
    '100644 blob\tpacks/pack/asset.bin', '100644 blob\tpacks/pack/workflow.pack.yaml',
    '040000 tree\tpacks/pack/workflows', '100644 blob\tpacks/pack/workflows/flow.yaml',
  ]);
  expect(treeModes(saved.commit)).toEqual(treeModes(f.sha));
  const release = await f.deploy.deploy({ workflowId: 'flow', actorId: 'trusted', candidateCommit: saved.commit, autoRun: false });
  expect(release.commit).toBe(saved.commit);
  const advancedResponse = await fetch(`${f.base}/pack-version`, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...f.body, requestId: 'save-after-release', sourceCommit: saved.commit, expectedSavedHead: saved.commit,
      changes: [{ ...f.body.changes[0], mode: '100644' }] }) });
  expect(advancedResponse.status).toBe(200); const advanced = await advancedResponse.json();
  expect(advanced.commit).not.toBe(release.commit);
  expect(f.remoteGit('rev-parse', `${release.commit}^{tree}`)).toBe(f.remoteGit('rev-parse', `${saved.commit}^{tree}`));
  const current = await fetch(`${f.base}/pack-version?version=current`);
  expect(current.status).toBe(200);
  expect(await current.json()).toMatchObject({ baseCommit: release.commit, draftHead: advanced.commit });
  const beforeDraftResponse = await fetch(`${f.base}/pack-version?version=draft`);
  expect(beforeDraftResponse.status).toBe(200);
  const beforeDraft = await beforeDraftResponse.json();
  expect(beforeDraft).toMatchObject({ baseCommit: advanced.commit, draftHead: advanced.commit,
    versionFiles: expect.arrayContaining([{ path: 'asset.bin', contentBase64: 'AAE=', mode: '100644' }]) });
  const savedTree = f.remoteGit('ls-tree', '-r', '-t', '--full-tree', advanced.commit);
  const pushesBefore = injection.pushes;
  const requestId = 'save-current-after-release';
  const rejected = await fetch(`${f.base}/pack-version`, { method: 'PUT',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...f.body, requestId,
      sourceVersion: 'current', sourceCommit: release.commit, expectedSavedHead: advanced.commit }),
  });
  expect(rejected.status).toBe(409);
  expect(await rejected.json()).toEqual({ error: 'Workflow operation could not be completed', code: 'VERSION_CONFLICT', requestId });
  expect(injection.pushes).toBe(pushesBefore);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(advanced.commit);
  expect(f.remoteGit('ls-tree', '-r', '-t', '--full-tree', 'refs/heads/wf/flow')).toBe(savedTree);
  const afterDraftResponse = await fetch(`${f.base}/pack-version?version=draft`);
  expect(afterDraftResponse.status).toBe(200);
  expect(await afterDraftResponse.json()).toEqual(beforeDraft);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(advanced.commit);
  expect(f.remoteGit('tag', '--list')).toBe('deploy/flow/#1\ndeploy/flow/#2');
  expect(await f.db.query('SELECT action, is_active FROM workflow_deploy_history')).toEqual([{ action: 'deploy', is_active: 0 }, { action: 'deploy', is_active: 1 }]);
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
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved.commit);
  const matched = await saveDraft('save-draft-matched', saved.commit); expect(matched.status).toBe(200);
  expect(f.remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe((await matched.json()).commit);
  expect(f.remoteGit('branch', '--list', 'saved/flow')).toBe('');
  expect(await f.db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
}, 30000);
