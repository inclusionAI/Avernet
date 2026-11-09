// @vitest-environment node
import { afterEach, beforeEach, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import Database from 'better-sqlite3';
import express from 'express';
import { stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createAutoHealRouter, type AutoHealRouteDependencies } from '../auto-heal.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { EditorDraftCasService } from '../../services/editor-draft-cas-service.js';
import { WorkflowUnifiedSaveService } from '../../services/workflow-unified-save-service.js';
import { createLocalRemote, runGit } from '../../services/__tests__/clawflows-git-client-fixture.js';

const original = { id: 'flow', title: 'before', nodes: [
  { id: 'start', title: 'Start', type: 'start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', type: 'end', executor: { type: 'end' }, dependsOn: ['start'] },
] };
const repositoryId = 'a'.repeat(64);
let root: string, remote: string, raw: Database.Database, db: SqliteDatabase, server: Server;
let deps: AutoHealRouteDependencies, draft: EditorDraftCasService, initialCommit: string, base: string;
const git = (...args: string[]) => runGit(root, ['--git-dir', remote, ...args]);
const post = async (endpoint: string, body: unknown) => {
  const response = await fetch(`${base}/api/auto-heal/${endpoint}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return { status: response.status, body: await response.json() as Record<string, any> };
};

beforeEach(async () => {
  root = mkdtempSync(path.join(tmpdir(), 'autoheal-route-real-')); remote = createLocalRemote(root);
  const seed = path.join(root, 'seed'); mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
  writeFileSync(path.join(seed, 'workflow.pack.yaml'), stringify({ id: 'pack', workflows: ['flow'] }));
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), stringify(original));
  writeFileSync(path.join(seed, 'scripts/run.sh'), 'preserved script\n');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'seed pack']); initialCommit = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', remote, `${initialCommit}:refs/heads/wf/flow`]);
  raw = new Database(path.join(root, 'workflow.sqlite'));
  raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE facade_bindings (id INTEGER PRIMARY KEY, command TEXT UNIQUE, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE cm_app_config (id INTEGER PRIMARY KEY, config_key TEXT, config_yaml TEXT, version INTEGER, enabled INTEGER, description TEXT, updated_by TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0);`);
  raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', 'pack', JSON.stringify(original), 'before', 1, 'editor', 1, 2);
  db = new SqliteDatabase(raw);
  const permissions = new BotWorkflowPermissionRepository(db); draft = new EditorDraftCasService(db, permissions);
  const gitConfig = { remoteUrl: remote, username: 'test', email: 'test@example.invalid' };
  const service = new WorkflowUnifiedSaveService({ draft, canEdit: (id, actor) => permissions.hasEditPermission(id, actor), repositoryId, config: gitConfig });
  // Test-only host binding. This is not the production bootstrap and does not
  // certify its authentication, parser, CORS, rate limit or logging behavior.
  deps = { db, workflowSpecRepo: new WorkflowSpecRepository(db), deployHistoryRepo: new WorkflowDeployHistoryRepository(db),
    botPermRepo: permissions, resolvePrincipal: async () => ({ actorId: 'editor' }), gitConfig,
    gitAuthor: { name: 'test', email: 'test@example.invalid' }, resolveUnifiedSave: async (id, actor) => {
      if (!await permissions.hasEditPermission(id, actor)) return null;
      const row = await draft.read(id, actor); const savedHead = git('rev-parse', `refs/heads/wf/${id}`);
      return { packId: row.packId ?? id, repositoryId, sourceVersion: 'draft', sourceCommit: savedHead,
        expectedSavedHead: savedHead, expectedDraftToken: row.draftToken, service };
    } };
  const app = express(); app.use(express.json()); app.use('/api/auto-heal', createAutoHealRouter(deps));
  await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  if (db) await db.close();
  rmSync(root, { recursive: true, force: true });
});

it('saves DB and wf, then deploys the exact candidate with real SQLite and a local Git remote', async () => {
  const saved = await post('unified-save', { requestId: 'heal-real-001', workflowId: 'flow', packId: 'pack', sourceVersion: 'draft',
    sourceCommit: initialCommit, expectedSavedHead: initialCommit, expectedDraftToken: (await draft.read('flow', 'editor')).draftToken,
    specJson: JSON.stringify({ ...original, title: 'repaired' }) });
  expect(saved).toMatchObject({ status: 200, body: { outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } } });
  const candidate = saved.body.evidence.commit;
  expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(candidate);
  expect(git('show', `${candidate}:scripts/run.sh`)).toBe('preserved script');
  const deployed = await post('editor-deploy/snapshot-v2', { workflowId: 'flow', packId: 'pack', requestId: 'deploy-real-001',
    candidateCommit: candidate, expectedDraftToken: saved.body.evidence.draftToken, snapshotProtocol: 'saved-tree-v2',
    expectedActiveCommit: '', confirmedFileDiff: true, autoRun: false });
  expect(deployed).toMatchObject({ status: 200, body: { deployed: true, candidateCommit: candidate, snapshotProtocol: 'saved-tree-v2' } });
  expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(candidate);
  expect(git('rev-parse', `refs/tags/${deployed.body.tagName}^{commit}`)).toBe(candidate);
  expect(raw.prepare('SELECT is_active, spec_json FROM workflow_deploy_history WHERE workflow_id = ?').get('flow')).toMatchObject({ is_active: 1, spec_json: expect.stringContaining('repaired') });
});

it('rejects snapshot-v2 deployment when the DB draft differs from the saved Git candidate', async () => {
  const changed = { ...original, title: 'DB-only changes' };
  raw.prepare('UPDATE workflow_specs SET spec_json = ?, title = ? WHERE workflow_id = ?').run(JSON.stringify(changed), changed.title, 'flow');
  const before = raw.prepare('SELECT * FROM workflow_specs WHERE workflow_id = ?').get('flow');
  const token = (await draft.read('flow', 'editor')).draftToken;
  const refs = git('show-ref');
  const result = await post('editor-deploy/snapshot-v2', { workflowId: 'flow', packId: 'pack', candidateCommit: initialCommit,
    expectedDraftToken: token, snapshotProtocol: 'saved-tree-v2', expectedActiveCommit: '', confirmedFileDiff: true, autoRun: false });
  expect(result).toMatchObject({ status: 409, body: { code: 'SNAPSHOT_DB_MISMATCH' } });
  expect(git('show-ref')).toBe(refs);
  expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  expect(raw.prepare('SELECT * FROM workflow_specs WHERE workflow_id = ?').get('flow')).toEqual(before);
});

it('retains execute-only access to legacy deploy while snapshot-v2 denies it', async () => {
  raw.prepare('UPDATE bot_workflow_permissions SET can_execute = 1, can_edit = 0 WHERE bot_owner_id = ?').run('editor');
  const denied = await post('editor-deploy/snapshot-v2', { workflowId: 'flow', candidateCommit: initialCommit,
    expectedDraftToken: 'b'.repeat(64), snapshotProtocol: 'saved-tree-v2', expectedActiveCommit: initialCommit, confirmedFileDiff: true, autoRun: false });
  expect(denied).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
  expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(initialCommit);
  const legacy = await post('deploy', { workflowId: 'flow', autoRun: false });
  expect(legacy).toMatchObject({ status: 200, body: { deployed: true } });
  expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1').get('flow')).toEqual({ n: 1 });
});

it('rejects a stale DB token before changing either SQLite or Git', async () => {
  const result = await post('unified-save', { requestId: 'heal-stale-001', workflowId: 'flow', packId: 'pack', sourceVersion: 'draft',
    sourceCommit: initialCommit, expectedSavedHead: initialCommit, expectedDraftToken: 'f'.repeat(64),
    specJson: JSON.stringify({ ...original, title: 'repaired' }) });
  expect(result).toMatchObject({ status: 409, body: { code: 'BASELINE_STALE' } });
  expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json)).toEqual(original);
  expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(initialCommit);
});

it('reports a partial save when edit authorization fails after DB confirmation, without advancing Git', async () => {
  const oldResolver = deps.resolveUnifiedSave!; let checks = 0;
  const interrupted = new WorkflowUnifiedSaveService({ draft, canEdit: async () => ++checks === 1,
    repositoryId, config: { remoteUrl: remote, username: 'test', email: 'test@example.invalid' } });
  deps.resolveUnifiedSave = async (id, actor) => {
    const binding = await oldResolver(id, actor); return binding ? { ...binding, service: interrupted } : null;
  };
  const result = await post('unified-save', { requestId: 'heal-partial-001', workflowId: 'flow', packId: 'pack', sourceVersion: 'draft',
    sourceCommit: initialCommit, expectedSavedHead: initialCommit, expectedDraftToken: (await draft.read('flow', 'editor')).draftToken,
    specJson: JSON.stringify({ ...original, title: 'repaired' }) });
  expect(result).toMatchObject({ status: 409, body: { outcome: 'partial', reconciliationRequired: true,
    evidence: { db: 'confirmed', git: 'not_started' } } });
  expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json).title).toBe('repaired');
  expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(initialCommit);
});
