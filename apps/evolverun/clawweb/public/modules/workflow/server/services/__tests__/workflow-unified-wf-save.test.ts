// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import { EditorDraftCasService } from '../editor-draft-cas-service.js';
import { WorkflowUnifiedSaveService } from '../workflow-unified-save-service.js';
import { projectWorkflowSnapshotYaml } from '../workflow-snapshot-projection.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
vi.setConfig({ testTimeout: 30_000 });
let root: string, remote: string, seed: string, base: string;
let raw: Database.Database, db: SqliteDatabase, cas: EditorDraftCasService;
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const rg = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const repoId = 'a'.repeat(64), spec = { id: 'flow', title: 'before', nodes: [{ id: 'n', executor: { type: 'agent' } }] };
const row = () => (raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id='flow'").get() as { spec_json: string }).spec_json;
const config = () => ({ remoteUrl: remote, username: 'test', email: 'test@example.invalid' });
function service(draft: Pick<EditorDraftCasService, 'read' | 'save'> = cas) {
  return new WorkflowUnifiedSaveService({ draft, canEdit: async () => true, config: config(), repositoryId: repoId });
}
async function request() {
  return { requestId: 'request_123', actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId: repoId,
    sourceVersion: 'draft' as const, sourceCommit: base, expectedSavedHead: base,
    expectedDraftToken: (await cas.read('flow', 'editor')).draftToken, specJson: JSON.stringify({ ...spec, title: 'after' }), changes: [] };
}
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'unified-wf-')); remote = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
  writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nversion: 1\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: before\nnodes: []\n');
  writeFileSync(path.join(seed, 'scripts/notify.sh'), '#!/bin/sh\necho notify\n'); chmodSync(path.join(seed, 'scripts/notify.sh'), 0o755);
  writeFileSync(path.join(seed, 'asset.bin'), Buffer.from([0, 255, 123])); git(seed, 'add', '.'); git(seed, 'commit', '-m', 'full pack');
  base = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${base}:refs/heads/wf/flow`);
  raw = new Database(':memory:'); raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0);`);
  raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', 'pack', JSON.stringify(spec), 'before', 1, 'editor', 1, 2);
  db = new SqliteDatabase(raw); cas = new EditorDraftCasService(db, new BotWorkflowPermissionRepository(db));
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true }); });
it('YAML-only unified save acknowledges real workflow_specs before the real wf push, preserving scripts and modes', async () => {
  const req = await request(), events: string[] = [], execute = execution.executeGit;
  vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
    if (args[0] === 'push') { events.push('git'); expect(JSON.parse(row())).toEqual(JSON.parse(req.specJson)); }
    return execute(args, options);
  });
  const result = await service({ read: cas.read.bind(cas), save: async input => { const result = await cas.save(input); events.push('db'); return result; } }).save(req, 'editor');
  expect(result.outcome).toBe('confirmed'); expect(events).toEqual(['db', 'git']);
  expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(result.evidence.commit);
  expect(git(root, 'ls-remote', remote, 'refs/heads/saved/flow')).toBe('');
  expect(projectWorkflowSnapshotYaml('flow', rg('show', `${result.evidence.commit}:workflows/flow.yaml`)).specJson).toBe(row());
  for (const file of ['scripts/notify.sh', 'asset.bin', 'workflow.pack.yaml']) expect(rg('ls-tree', result.evidence.commit!, file)).toBe(rg('ls-tree', base, file));
  expect(rg('ls-tree', result.evidence.commit!, 'scripts/notify.sh')).toMatch(/^100755/);
});
it('actual SQLite write failure never invokes git push', async () => {
  const req = await request(), before = row(), execute = execution.executeGit; let pushes = 0;
  raw.exec("CREATE TRIGGER fail_save BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'db failure'); END");
  vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => { if (args[0] === 'push') pushes++; return execute(args, options); });
  const result = await service().save(req, 'editor');
  expect(result).toMatchObject({ outcome: 'unknown', evidence: { db: 'unknown', git: 'not_started' } });
  expect(pushes).toBe(0); expect(row()).toBe(before); expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(base);
});
it('rejects stale wf lease before DB writes rather than overwriting another saved Pack', async () => {
  const req = await request(), before = row(); git(seed, 'commit', '--allow-empty', '-m', 'concurrent save');
  const concurrent = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${concurrent}:refs/heads/wf/flow`);
  expect(await service().save(req, 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(row()).toBe(before); expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(concurrent);
});
it('does not write DB when legacy read priority selects a tree missing wf notify scripts', async () => {
  const req = await request(), before = row(); git(seed, 'rm', 'scripts/notify.sh'); git(seed, 'commit', '-m', 'legacy missing scripts');
  const legacy = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${legacy}:refs/heads/saved/flow`);
  expect(await service().save({ ...req, sourceCommit: legacy }, 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(row()).toBe(before); expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(base); expect(rg('rev-parse', 'refs/heads/saved/flow')).toBe(legacy);
});

it.each(['workflows/flow.yaml', 'custom/flow.yaml'])('replaces malformed Git YAML at manifest-declared %s with the DB editor projection', async workflowPath => {
  if (workflowPath.startsWith('custom/')) {
    mkdirSync(path.join(seed, 'custom'));
    git(seed, 'rm', 'workflows/flow.yaml');
    writeFileSync(path.join(seed, 'workflow.pack.yaml'), `id: pack\nversion: 1\nworkflows:\n  - id: flow\n    file: ${workflowPath}\n`);
  }
  writeFileSync(path.join(seed, workflowPath), 'id: flow\ntitle: [broken\n');
  chmodSync(path.join(seed, workflowPath), 0o755);
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'malformed workflow');
  base = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${base}:refs/heads/wf/flow`);
  const req = await request();
  const result = await service().save(req, 'editor');
  expect(result.outcome).toBe('confirmed');
  expect(projectWorkflowSnapshotYaml('flow', rg('show', `${result.evidence.commit}:${workflowPath}`)).specJson).toBe(row());
  expect(rg('ls-tree', result.evidence.commit!, workflowPath)).toMatch(/^100755/);
  for (const file of ['scripts/notify.sh', 'asset.bin', 'workflow.pack.yaml']) {
    expect(rg('ls-tree', result.evidence.commit!, file)).toBe(rg('ls-tree', base, file));
  }
});

it.each(['id: another\nnodes: []\n', 'id: flow\ntitle: [broken\0\n'])('does not replace a wrong identity or binary workflow source', async source => {
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), source);
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'unsafe workflow');
  base = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${base}:refs/heads/wf/flow`);
  const before = row();
  expect(await service().save(await request(), 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(row()).toBe(before); expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(base);
});

it('does not repair malformed YAML using a stale DB token or stale Git lease', async () => {
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: [broken\n');
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'malformed workflow');
  base = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${base}:refs/heads/wf/flow`);
  const req = await request(), before = row();
  expect(await service().save({ ...req, expectedDraftToken: 'f'.repeat(64) }, 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  git(seed, 'commit', '--allow-empty', '-m', 'concurrent save');
  const changed = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${changed}:refs/heads/wf/flow`);
  expect(await service().save(req, 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(row()).toBe(before); expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(changed);
});
