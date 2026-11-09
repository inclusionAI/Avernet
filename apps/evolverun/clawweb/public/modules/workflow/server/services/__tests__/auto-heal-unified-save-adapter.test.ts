// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { Request, Response, Router } from 'express';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { createAutoHealRouter, type AutoHealRouteDependencies } from '../../routes/auto-heal.js';
import { EditorDraftCasService } from '../editor-draft-cas-service.js';
import { WorkflowUnifiedSaveService } from '../workflow-unified-save-service.js';
import { withPreparedPackCandidateForUnifiedSave } from '../pack-candidate-save.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';

const original = { id: 'flow', title: 'before', nodes: [{ id: 'n', executor: { type: 'agent' } }] };
const repositoryId = 'a'.repeat(64);
function post(router: Router, url: string, body: unknown): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const req = { method: 'POST', url, body, headers: {}, get: () => undefined, header: () => undefined } as unknown as Request;
    let status = 200;
    const res = { set() { return this; }, status(code: number) { status = code; return this; },
      json(value: unknown) { resolve({ status, body: value }); return this; } } as Response;
    router(req, res, (error?: unknown) => reject(error ?? new Error('No route response')));
  });
}

describe('explicit AutoHeal unified-save adapter', () => {
  let root: string, bare: string, seed: string, commit: string, raw: Database.Database, db: SqliteDatabase;
  let deps: AutoHealRouteDependencies, cas: EditorDraftCasService;
  let body: { requestId: string; workflowId: string; packId: string; sourceVersion: 'draft'; sourceCommit: string;
    expectedSavedHead: string | null; expectedDraftToken: string; specJson: string };
  const git = (...args: string[]) => runGit(root, ['--git-dir', bare, ...args]);
  const row = () => raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string };
  const request = () => post(createAutoHealRouter(deps), '/unified-save', body);
  beforeEach(async () => {
    root = mkdtempSync(path.join(tmpdir(), 'autoheal-unified-')); bare = createLocalRemote(root); seed = path.join(root, 'seed');
    mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
    writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\nnodes: []\n');
    writeFileSync(path.join(seed, 'scripts/run.sh'), 'preserved\n');
    writeFileSync(path.join(seed, 'resource.bin'), Buffer.from([0, 255, 1, 128]));
    runGit(seed, ['add', '.']); runGit(seed, ['update-index', '--chmod=+x', 'scripts/run.sh']); runGit(seed, ['commit', '-m', 'pack']); commit = runGit(seed, ['rev-parse', 'HEAD']);
    runGit(seed, ['push', bare, `${commit}:refs/heads/wf/flow`]);
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0);`);
    raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', 'pack', JSON.stringify(original), 'before', 1, 'editor', 1, 2);
    db = new SqliteDatabase(raw); const perms = new BotWorkflowPermissionRepository(db); cas = new EditorDraftCasService(db, perms);
    const service = new WorkflowUnifiedSaveService({ draft: cas, canEdit: (id, actor) => perms.hasEditPermission(id, actor),
      canWritePack: async () => true, repositoryId, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' } });
    body = { requestId: 'heal-request-1', workflowId: 'flow', packId: 'pack', sourceVersion: 'draft', sourceCommit: commit,
      expectedSavedHead: commit, expectedDraftToken: (await cas.read('flow', 'editor')).draftToken,
      specJson: JSON.stringify({ ...original, title: 'repair' }) };
    deps = { db, workflowSpecRepo: new WorkflowSpecRepository(db), deployHistoryRepo: new WorkflowDeployHistoryRepository(db),
      botPermRepo: perms, resolvePrincipal: async () => ({ actorId: 'editor' }), gitConfig: { remoteUrl: '', username: '', email: '' },
      gitAuthor: { name: 'test', email: 'test@example.invalid' }, resolveUnifiedSave: async () => ({ packId: 'pack', repositoryId,
        sourceVersion: 'draft', sourceCommit: commit, expectedSavedHead: commit, expectedDraftToken: body.expectedDraftToken, service }) };
  });
  afterEach(async () => { vi.restoreAllMocks(); if (db) await db.close(); rmSync(root, { recursive: true, force: true }); });

  it('uses the real unified service for one DB/Git acknowledgement and preserves the Pack', async () => {
    const result = await request();
    expect(result).toMatchObject({ status: 200, body: { outcome: 'confirmed', reconciliationRequired: false,
      evidence: { db: 'confirmed', git: 'confirmed' } } });
    expect(JSON.parse(row().spec_json).title).toBe('repair');
    expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(result.body.evidence.commit);
    expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
    expect(git('show', `${result.body.evidence.commit}:scripts/run.sh`)).toBe('preserved');
    expect(result.body.evidence.commit).not.toBe(commit);
    for (const file of ['workflow.pack.yaml', 'scripts/run.sh', 'resource.bin', 'README']) {
      expect(git('ls-tree', result.body.evidence.commit, file)).toBe(git('ls-tree', commit, file));
    }
    expect(git('ls-tree', result.body.evidence.commit, 'scripts/run.sh')).toMatch(/^100755 /);
  });
  it('fails closed without a binding and never falls back to a DB save', async () => {
    deps.resolveUnifiedSave = undefined;
    expect(await request()).toMatchObject({ status: 503, body: { code: 'UNIFIED_SAVE_UNAVAILABLE' } });
    expect(JSON.parse(row().spec_json)).toEqual(original); expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(commit); expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
  });
  it.each(['packId', 'sourceCommit', 'expectedSavedHead', 'expectedDraftToken'] as const)('rejects a stale caller %s before writes', async field => {
    const resolve = deps.resolveUnifiedSave!; const binding = await resolve('flow', 'editor');
    deps.resolveUnifiedSave = async () => binding;
    (body as Record<string, unknown>)[field] = field === 'packId' ? 'other' : 'f'.repeat(64);
    expect(await request()).toMatchObject({ status: 409, body: { code: 'BASELINE_STALE' } });
    expect(JSON.parse(row().spec_json)).toEqual(original); expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(commit); expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
  });
  it('rejects unauthorized and lossy documents without writing', async () => {
    deps.resolvePrincipal = async () => null;
    expect((await request()).status).toBe(401);
    deps.resolvePrincipal = async () => ({ actorId: 'editor' });
    body.specJson = body.specJson.replace('"repair"', '"repair", "large": 9007199254740993');
    expect((await request()).status).toBe(400); expect(JSON.parse(row().spec_json)).toEqual(original);
  });
  it('reports denied Pack policy without pretending the DB was saved', async () => {
    const resolve = deps.resolveUnifiedSave!; const b = (await resolve('flow', 'editor'))!;
    const policy = vi.fn(async () => false);
    const permissions = new BotWorkflowPermissionRepository(db);
    const denied = new WorkflowUnifiedSaveService({ draft: cas, canEdit: (id, actor) => permissions.hasEditPermission(id, actor),
      canWritePack: policy, repositoryId, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' } });
    deps.resolveUnifiedSave = async () => ({ ...b, service: denied });
    expect(await request()).toMatchObject({ status: 409, body: { outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } } });
    expect(policy).toHaveBeenCalledOnce();
    expect(JSON.parse(row().spec_json)).toEqual(original); expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(commit); expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
  });
  it('uses workflow edit permission when no additional Pack policy is configured', async () => {
    const b = (await deps.resolveUnifiedSave!('flow', 'editor'))!;
    const permissions = new BotWorkflowPermissionRepository(db);
    const service = new WorkflowUnifiedSaveService({ draft: cas, canEdit: (id, actor) => permissions.hasEditPermission(id, actor),
      repositoryId, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' } });
    deps.resolveUnifiedSave = async () => ({ ...b, service });
    const result = await request();
    expect(result).toMatchObject({ status: 200, body: { outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } } });
    expect(JSON.parse(row().spec_json).title).toBe('repair');
    expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(result.body.evidence.commit);
    expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
  });
  it('reports a partial result when Pack permission is revoked after the DB write', async () => {
    const b = (await deps.resolveUnifiedSave!('flow', 'editor'))!; let checks = 0;
    const service = new WorkflowUnifiedSaveService({ draft: cas, canEdit: async () => true,
      canWritePack: async () => ++checks === 1, repositoryId,
      config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' } });
    deps.resolveUnifiedSave = async () => ({ ...b, service });
    expect(await request()).toMatchObject({ status: 409, body: { outcome: 'partial', reconciliationRequired: true,
      evidence: { db: 'confirmed', git: 'not_started' } } });
    expect(JSON.parse(row().spec_json).title).toBe('repair');
    expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(commit); expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
  });
  it('reports unknown when a real Git push succeeds but its acknowledgement is lost', async () => {
    const b = (await deps.resolveUnifiedSave!('flow', 'editor'))!;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        await push(); throw new Error('private lost acknowledgement');
      }));
    const service = new WorkflowUnifiedSaveService({ draft: cas, canEdit: async () => true, canWritePack: async () => true,
      repositoryId, prepare, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' } });
    deps.resolveUnifiedSave = async () => ({ ...b, service });
    const result = await request();
    expect(result).toMatchObject({ status: 409, body: { outcome: 'unknown', reconciliationRequired: true,
      evidence: { db: 'confirmed', git: 'unknown' } } });
    expect(JSON.stringify(result)).not.toContain('private');
    expect(JSON.parse(row().spec_json).title).toBe('repair');
    expect(git('rev-parse', 'refs/heads/wf/flow')).toBe(result.body.evidence.commit);
    expect(() => git('rev-parse', 'refs/heads/saved/flow')).toThrow();
  });
  it.each(['throws', 'malformed', 'partial-without-evidence'])('treats %s service result as unknown, not success', async mode => {
    const resolve = deps.resolveUnifiedSave!; const b = (await resolve('flow', 'editor'))!;
    deps.resolveUnifiedSave = async () => ({ ...b, service: { save: async () => {
      if (mode === 'throws') throw new Error('private content');
      return mode === 'partial-without-evidence'
        ? { outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } }
        : { outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } };
    } } });
    const result = await request();
    expect(result).toMatchObject({ status: 409, body: { outcome: 'unknown', reconciliationRequired: true } });
    expect(JSON.stringify(result)).not.toContain('private content');
  });
  it('rejects unresolved legacy operations before the unified service runs', async () => {
    raw.exec("CREATE TABLE clawweb_pack_operations (request_id TEXT, workflow_id TEXT, phase TEXT, lock_workflow_id TEXT); INSERT INTO clawweb_pack_operations VALUES ('old','flow','unknown','flow')");
    expect(await request()).toMatchObject({ status: 409, body: { code: 'LEGACY_PACK_OPERATION_UNRESOLVED' } });
    expect(JSON.parse(row().spec_json)).toEqual(original);
  });
});
