// @vitest-environment node
// Local-only: requires Node >=20, Git, and workspace dependencies including
// better-sqlite3. Diagnosis is a fixture; no model or remote host is contacted.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { Request, Response, Router } from 'express';
import Database from 'better-sqlite3';
import { parse, stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import * as deployGit from '../../services/auto-heal-deploy-git.js';
import * as gitClient from '../../services/clawflows-git-client.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { createAutoHealRouter, type AutoHealRouteDependencies } from '../auto-heal.js';
import { EditorDraftCasService } from '../../services/editor-draft-cas-service.js';

vi.setConfig({ testTimeout: 45_000, hookTimeout: 30_000 });
const id = 'heal-local';
const author = { name: 'Local Fixture', email: 'fixture@example.invalid' };
const original = { id, version: '1.0.0', title: 'Before diagnosis', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
const fixed = { ...original, title: 'Diagnosed repair' };
const git = (cwd: string, ...args: string[]) => execFileSync('git', args, {
  cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0' },
}).trim();
function post(router: Router, endpoint: string, body: unknown): Promise<{ status: number; body: Record<string, any> }> {
  return new Promise((resolve, reject) => {
    const req = { method: 'POST', url: `/${endpoint}`, body, headers: {}, get: () => undefined, header: () => undefined } as unknown as Request;
    let status = 200;
    const res = { status(code: number) { status = code; return this; },
      json(payload: Record<string, any>) { resolve({ status, body: payload }); return this; } } as Response;
    router(req, res, (error?: unknown) => reject(error ?? new Error('No route response')));
  });
}

describe('AutoHeal local SQLite + bare Git compatibility', () => {
  let root: string, remote: string, seed: string, candidate: string;
  let raw: Database.Database, db: SqliteDatabase, deps: AutoHealRouteDependencies;
  const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
  const save = () => post(createAutoHealRouter(deps), 'save', { workflowId: id, spec: fixed, diagnosisId: 'diagnosis-local' });
  const deploy = (endpoint = 'deploy', extra: Record<string, unknown> = {}) => post(createAutoHealRouter(deps), endpoint,
    { workflowId: id, packId: 'pack-local', diagnosisId: 'diagnosis-local', requestId: 'local-deploy-001', autoRun: false, ...extra });
  const history = () => raw.prepare('SELECT deploy_number, action, is_active, spec_json FROM workflow_deploy_history ORDER BY deploy_number').all() as Array<{ deploy_number: number; action: string; is_active: number; spec_json: string }>;
  const savedSpec = () => JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get(id) as { spec_json: string }).spec_json);
  const activate = (commit: string) => {
    const tag = `deploy/${id}/#1`;
    git(seed, 'tag', tag, commit);
    git(seed, 'push', remote, `refs/tags/${tag}`);
    raw.prepare(`INSERT INTO workflow_deploy_history
      (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES (?, ?, 1, 1, ?, 'deploy', ?, 1)`).run('pack-local', id, tag, JSON.stringify(original));
  };

  beforeEach(async () => {
    vi.stubEnv('GIT_ALLOW_PROTOCOL', 'file');
    vi.stubEnv('GIT_CONFIG_NOSYSTEM', '1');
    vi.stubEnv('GIT_CONFIG_GLOBAL', '/dev/null');
    vi.stubEnv('GIT_TERMINAL_PROMPT', '0');
    root = mkdtempSync(path.join(tmpdir(), 'autoheal-local-compat-'));
    remote = path.join(root, 'remote.git'); seed = path.join(root, 'seed');
    git(root, 'init', '--bare', '--initial-branch=main', remote);
    git(root, 'init', '--initial-branch=main', seed);
    git(seed, 'config', 'user.name', author.name); git(seed, 'config', 'user.email', author.email);
    mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
    writeFileSync(path.join(seed, 'workflow.pack.yaml'), stringify({ id: 'pack-local', version: '1.0.0', workflows: [{ id, file: `workflows/${id}.yaml` }] }));
    writeFileSync(path.join(seed, 'workflows', `${id}.yaml`), stringify(original));
    writeFileSync(path.join(seed, 'scripts/run.sh'), 'original script\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'local fixture'); candidate = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, 'HEAD:refs/heads/main', `HEAD:refs/heads/wf/${id}`);
    raw = new Database(path.join(root, 'workflow.sqlite'));
    raw.exec(`CREATE TABLE cm_app_config (id INTEGER PRIMARY KEY, config_key TEXT UNIQUE, config_yaml TEXT, version INTEGER DEFAULT 1, enabled INTEGER DEFAULT 1, description TEXT, updated_by TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER DEFAULT 0, can_execute INTEGER DEFAULT 0, can_edit INTEGER DEFAULT 0, env TEXT DEFAULT 'test', gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
    db = new SqliteDatabase(raw);
    const deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
    const workflowSpecRepo = new WorkflowSpecRepository(db, deployHistoryRepo);
    const botPermRepo = new BotWorkflowPermissionRepository(db);
    await workflowSpecRepo.upsert(id, 'pack-local', JSON.stringify(original), 'operator');
    await botPermRepo.upsert({ bot_id: null, bot_owner_id: 'operator', workflow_id: id, can_view: 1, can_execute: 1, can_edit: 1 });
    deps = { db, workflowSpecRepo, deployHistoryRepo, botPermRepo, authorizeSnapshotDeploy: async () => true, resolvePrincipal: async () => ({ actorId: 'operator' }),
      gitConfig: { remoteUrl: remote, username: author.name, email: author.email }, gitAuthor: author, triggerRun: vi.fn() };
  });
  afterEach(async () => { vi.restoreAllMocks(); vi.unstubAllEnvs(); if (db) await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  it('saves a diagnosis repair to SQLite and wf, then tags that exact Pack', async () => {
    expect(await save()).toMatchObject({ status: 200, body: { ok: true, saved: true, workflowId: id, packId: 'pack-local' } });
    expect(savedSpec()).toEqual(fixed);
    const savedHead = remoteGit('rev-parse', `refs/heads/wf/${id}`);
    expect(savedHead).not.toBe(candidate);
    expect(remoteGit('tag', '-l')).toBe(''); expect(history()).toEqual([]);
    const result = await deploy();
    expect(result).toMatchObject({ status: 200, body: { ok: true, deployed: true, workflowId: id, packId: 'pack-local', candidateCommit: savedHead, deployNumber: 1 } });
    const commit = result.body.commit as string;
    expect(commit).toBe(savedHead);
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(savedHead);
    expect(remoteGit('rev-parse', `refs/tags/${result.body.tagName}^{commit}`)).toBe(savedHead);
    expect(parse(remoteGit('show', `${commit}:workflows/${id}.yaml`))).toEqual(fixed);
    expect(remoteGit('show', `${commit}:scripts/run.sh`)).toBe('original script');
    expect(history()).toHaveLength(1); expect(history()[0]).toMatchObject({ deploy_number: 1, action: 'deploy', is_active: 1 });
    expect(JSON.parse(history()[0].spec_json)).toMatchObject(fixed);
    expect(deps.triggerRun).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE name = 'clawweb_pack_operations'").all()).toEqual([]);
  });
  it('denies untrusted or unauthorized actors without persisting or publishing', async () => {
    deps.resolvePrincipal = async () => null;
    expect((await save()).status).toBe(401); expect((await deploy()).status).toBe(401); expect((await deploy('editor-deploy')).status).toBe(401);
    deps.resolvePrincipal = async () => ({ actorId: 'other' });
    expect(await save()).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(await deploy()).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(await deploy('editor-deploy')).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(savedSpec()).toEqual(original); expect(history()).toEqual([]);
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(candidate); expect(remoteGit('tag', '-l')).toBe('');
  });
  it('rejects direct snapshot-v2 deploy for view+execute without edit while legacy AutoHeal remains allowed', async () => {
    const { draftToken } = await new EditorDraftCasService(db, deps.botPermRepo).read(id, 'operator');
    await deps.botPermRepo!.upsert({ bot_id: null, bot_owner_id: 'operator', workflow_id: id, can_view: 1, can_execute: 1, can_edit: 0 });
    const before = remoteGit('show-ref');
    const snapshot = await deploy('editor-deploy/snapshot-v2', { candidateCommit: candidate, expectedDraftToken: draftToken,
      expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' });
    expect(snapshot).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(remoteGit('show-ref')).toBe(before); expect(history()).toEqual([]);
    expect(await deploy()).toMatchObject({ status: 200, body: { ok: true, deployed: true } });
  });

  it.each(['missing', 'denied', 'error'] as const)('uses workflow edit plus the optional host policy for exact deployment: %s', async mode => {
    const { draftToken } = await new EditorDraftCasService(db, deps.botPermRepo).read(id, 'operator');
    deps.authorizeSnapshotDeploy = mode === 'missing' ? undefined : async () => {
      if (mode === 'error') throw new Error('private host failure');
      return false;
    };
    const before = remoteGit('show-ref');
    const response = await deploy('editor-deploy/snapshot-v2', { candidateCommit: candidate, expectedDraftToken: draftToken,
      expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' });
    if (mode === 'missing') {
      expect(response).toMatchObject({ status: 200, body: { ok: true, candidateCommit: candidate } });
      expect(history()).toHaveLength(1);
    } else {
      expect(response).toMatchObject({ status: mode === 'error' ? 503 : 403,
        body: { code: mode === 'error' ? 'SNAPSHOT_DEPLOY_AUTHORIZATION_UNAVAILABLE' : 'SNAPSHOT_DEPLOY_FORBIDDEN' } });
      expect(JSON.stringify(response.body)).not.toContain('private host failure');
      expect(remoteGit('show-ref')).toBe(before); expect(history()).toEqual([]);
    }
    expect(await deploy()).toMatchObject({ status: 200, body: { ok: true, deployed: true } });
  });

  it('reports a real SQLite write failure without changing the draft or Git', async () => {
    raw.exec("CREATE TRIGGER deny_save BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'fixture failure'); END");
    expect(await save()).toMatchObject({ status: 500, body: { code: 'SQLITE_CONSTRAINT_TRIGGER' } });
    expect(savedSpec()).toEqual(original); expect(history()).toEqual([]);
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(candidate); expect(remoteGit('tag', '-l')).toBe('');
  });
  it('treats a missing deployed path as a P0 tree rejection without release side effects', async () => {
    writeFileSync(path.join(seed, 'scripts/active-only.sh'), 'keep\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'deployed content');
    const active = git(seed, 'rev-parse', 'HEAD');
    activate(active);
    expect((await save()).status).toBe(200);
    const savedHead = remoteGit('rev-parse', `refs/heads/wf/${id}`);
    const before = remoteGit('show-ref');
    const { draftToken } = await new EditorDraftCasService(db, deps.botPermRepo).read(id, 'operator');
    const result = await deploy('editor-deploy', { guard: false, preserveTree: false, expectedDraftToken: draftToken });
    expect(result).toMatchObject({ status: 409, body: { code: 'DEPLOY_TREE_CONFLICT', requestId: 'local-deploy-001' } });
    expect(JSON.stringify(result.body)).not.toContain('active-only.sh');
    expect(remoteGit('show-ref')).toBe(before);
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(savedHead);
    expect(history()).toHaveLength(1); expect(history()[0]).toMatchObject({ is_active: 1 });
    expect(savedSpec()).toEqual(fixed);
  });
  it.each([undefined, null, '', 'A'.repeat(64), '0'.repeat(63), 42])('rejects malformed Editor draft token %s without Git or DB writes', async expectedDraftToken => {
    const refs = remoteGit('show-ref'); const before = savedSpec();
    const clone = vi.spyOn(gitClient, 'initClawflowsReadRepo');
    const result = await deploy('editor-deploy', { expectedDraftToken });
    expect(result).toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(clone).not.toHaveBeenCalled(); expect(remoteGit('show-ref')).toBe(refs);
    expect(savedSpec()).toEqual(before); expect(history()).toEqual([]);
  });
  it.each(['changed', 'absent'])('rejects an obsolete confirmed draft (%s) before Git with no release effects', async change => {
    const { draftToken } = await new EditorDraftCasService(db, deps.botPermRepo).read(id, 'operator');
    if (change === 'changed') raw.prepare('UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?').run(JSON.stringify(fixed), id);
    else raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run(id);
    const refs = remoteGit('show-ref'); const rows = raw.prepare('SELECT * FROM workflow_specs').all();
    const clone = vi.spyOn(gitClient, 'initClawflowsReadRepo');
    expect(await deploy('editor-deploy', { expectedDraftToken: draftToken })).toMatchObject({ status: 409, body: { code: 'DRAFT_CONFLICT', requestId: 'local-deploy-001' } });
    expect(clone).not.toHaveBeenCalled(); expect(remoteGit('show-ref')).toBe(refs);
    expect(raw.prepare('SELECT * FROM workflow_specs').all()).toEqual(rows); expect(history()).toEqual([]);
  });
  it('rechecks the confirmed row after Git initialization and rejects a concurrent edit before release preparation', async () => {
    const { draftToken } = await new EditorDraftCasService(db, deps.botPermRepo).read(id, 'operator');
    const refs = remoteGit('show-ref'); const clone = gitClient.initClawflowsReadRepo;
    vi.spyOn(gitClient, 'initClawflowsReadRepo').mockImplementationOnce(async (...args) => {
      await clone(...args);
      raw.prepare('UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?').run(JSON.stringify(fixed), id);
    });
    const prepare = vi.spyOn(deployGit, 'prepareDeployCandidate');
    expect(await deploy('editor-deploy', { expectedDraftToken: draftToken })).toMatchObject({ status: 409, body: { code: 'DRAFT_CONFLICT', requestId: 'local-deploy-001' } });
    expect(prepare).not.toHaveBeenCalled(); expect(remoteGit('show-ref')).toBe(refs);
    expect(savedSpec()).toEqual(fixed); expect(history()).toEqual([]);
  });
  it('never publishes the new DB draft if it changes after the last pre-push check', async () => {
    const { draftToken } = await new EditorDraftCasService(db, deps.botPermRepo).read(id, 'operator');
    const push = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      raw.prepare('UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?').run(JSON.stringify(fixed), id);
      return push(...args);
    });
    expect(await deploy('editor-deploy', { expectedDraftToken: draftToken })).toMatchObject({ status: 503, body: { code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' } });
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(candidate);
    expect(remoteGit('rev-parse', `refs/tags/deploy/${id}/#1^{commit}`)).toBe(candidate);
    expect(parse(remoteGit('show', `${candidate}:workflows/${id}.yaml`))).toEqual(original);
    expect(savedSpec()).toEqual(fixed); expect(history()).toEqual([]);
  });
  it('rejects AutoHeal deletion of a production resource even when the body requests a guard', async () => {
    writeFileSync(path.join(seed, 'scripts/active-only.sh'), 'old asset\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'different active tree');
    activate(git(seed, 'rev-parse', 'HEAD'));
    expect((await save()).status).toBe(200);
    const savedHead = remoteGit('rev-parse', `refs/heads/wf/${id}`);
    const before = remoteGit('show-ref');
    const result = await deploy('deploy', { guard: true, preserveTree: true });
    expect(result).toMatchObject({ status: 409, body: { code: 'DEPLOY_TREE_CONFLICT' } });
    expect(remoteGit('show-ref')).toBe(before);
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(savedHead);
    expect(history()).toHaveLength(1); expect(history()[0]).toMatchObject({ is_active: 1 });
  });
  it('deploys the inspected snapshot even if wf advances concurrently before publication', async () => {
    expect((await save()).status).toBe(200);
    const savedHead = remoteGit('rev-parse', `refs/heads/wf/${id}`);
    git(seed, 'fetch', remote, `refs/heads/wf/${id}`);
    git(seed, 'reset', '--hard', 'FETCH_HEAD');
    writeFileSync(path.join(seed, 'scripts/run.sh'), 'newer saved script\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'concurrent save');
    const newer = git(seed, 'rev-parse', 'HEAD');
    const push = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      git(seed, 'push', remote, `HEAD:refs/heads/wf/${id}`);
      return push(...args);
    });
    const result = await deploy();
    expect(result).toMatchObject({ status: 200, body: { candidateCommit: savedHead, commit: savedHead } });
    expect(remoteGit('rev-parse', `refs/heads/wf/${id}`)).toBe(newer);
    expect(remoteGit('show', `${savedHead}:scripts/run.sh`)).toBe('original script');
    expect(remoteGit('show', `${newer}:scripts/run.sh`)).toBe('newer saved script');
    expect(remoteGit('rev-parse', `refs/tags/${result.body.tagName}^{commit}`)).toBe(savedHead);
    expect(history()).toHaveLength(1);
  });
  it('keeps a lost acknowledgement distinct from rejection after a real tag push', async () => {
    expect((await save()).status).toBe(200);
    const realExecute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await realExecute(args, options);
      if (args[0] === 'push') throw new Error('local lost acknowledgement');
      return result;
    });
    const result = await deploy();
    expect(result).toMatchObject({ status: 503, body: { code: 'DEPLOY_OUTCOME_UNKNOWN', requestId: 'local-deploy-001' } });
    const active = remoteGit('rev-parse', `refs/heads/wf/${id}`);
    expect(active).not.toBe(candidate);
    expect(remoteGit('rev-parse', `refs/tags/deploy/${id}/#1^{commit}`)).toBe(active);
    expect(parse(remoteGit('show', `${active}:workflows/${id}.yaml`))).toEqual(fixed);
    expect(history()).toEqual([]); expect(deps.triggerRun).not.toHaveBeenCalled();
  });
});
