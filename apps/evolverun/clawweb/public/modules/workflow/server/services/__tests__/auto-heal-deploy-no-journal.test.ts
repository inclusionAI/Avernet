// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { parse, stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import * as deployment from '../auto-heal-deploy-git.js';
import { runGit } from './clawflows-git-client-fixture.js';
const git = (cwd: string, ...args: string[]) => runGit(cwd, args);
const author = { name: 'Test', email: 'test@example.invalid' };
const spec = { id: 'flow', version: '1.0.0', title: 'Old Git YAML', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
describe('deployment without a Pack operation table', () => {
  let root: string, remote: string, work: string, saved: string, raw: Database.Database, db: SqliteDatabase;
  let service: AutoHealDeployService;
  const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
  beforeEach(async () => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT DEFAULT 'test', gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
    db = new SqliteDatabase(raw); const history = new WorkflowDeployHistoryRepository(db), specs = new WorkflowSpecRepository(db, history), permissions = new BotWorkflowPermissionRepository(db);
    await specs.upsert('flow', 'pack', JSON.stringify({ content: stringify({ ...spec, title: 'Database YAML' }) }), 'user');
    await permissions.upsert({ bot_id: null, bot_owner_id: 'user', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 0 });
    root = mkdtempSync(path.join(tmpdir(), 'deploy-no-journal-')); remote = path.join(root, 'remote.git'); work = path.join(root, 'work');
    git(root, 'init', '--bare', '--initial-branch=main', remote); git(root, 'init', '--initial-branch=main', work);
    git(work, 'config', 'user.name', author.name); git(work, 'config', 'user.email', author.email);
    mkdirSync(path.join(work, 'workflows')); mkdirSync(path.join(work, 'scripts'));
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'pack', version: '1.0.0', workflows: [{ id: 'flow', file: 'workflows/flow.yaml' }] }));
    writeFileSync(path.join(work, 'workflows/flow.yaml'), stringify(spec));
    writeFileSync(path.join(work, 'scripts/run.sh'), '#!/bin/sh\necho saved\n', { mode: 0o755 });
    writeFileSync(path.join(work, 'asset.bin'), Buffer.from([0, 1, 255, 10]));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'saved pack'); saved = git(work, 'rev-parse', 'HEAD');
    git(work, 'remote', 'add', 'origin', remote); git(work, 'push', 'origin', 'HEAD:refs/heads/main', 'HEAD:refs/heads/saved/flow');
    service = new AutoHealDeployService(db, specs, history, permissions, { remoteUrl: remote, username: author.name, email: author.email, token: '' }, author);
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  it('publishes database YAML and preserves the complete saved tree without a journal table', async () => {
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', candidateCommit: saved, autoRun: false });
    expect(result).toMatchObject({ ok: true, deployed: true, packId: 'pack', deployNumber: 1, version: 1, candidateCommit: saved });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{}')).toBe(result.commit);
    expect(remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(saved);
    expect(parse(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).title).toBe('Database YAML');
    for (const file of ['asset.bin', 'scripts/run.sh', 'workflow.pack.yaml']) expect(remoteGit('rev-parse', `${result.commit}:${file}`)).toBe(remoteGit('rev-parse', `${saved}:${file}`));
    expect(remoteGit('ls-tree', result.commit!, 'scripts/run.sh')).toMatch(/^100755 /);
    const row = raw.prepare('SELECT * FROM workflow_deploy_history').get() as { spec_json: string; is_active: number };
    expect(JSON.parse(row.spec_json).content).toContain('Database YAML'); expect(row.is_active).toBe(1);
    expect(raw.prepare('SELECT version FROM workflow_specs').get()).toEqual({ version: 1 });
  });
  it('rejects unauthorized actors and stale candidates before publishing', async () => {
    await expect(service.deploy({ workflowId: 'flow', actorId: 'other' })).rejects.toMatchObject({ code: 'FORBIDDEN' });
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', candidateCommit: 'f'.repeat(40) })).rejects.toMatchObject({ code: 'CANDIDATE_NOT_SAVED' });
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', packId: 'wrong' })).rejects.toMatchObject({ code: 'PACK_ID_MISMATCH' });
    expect(remoteGit('branch', '--list', 'wf/flow')).toBe('');
  });
  it('rejects invalid DB YAML before publishing', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ content: 'id: wrong\nnodes: []' }));
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'INVALID_WORKFLOW' });
    expect(remoteGit('tag', '-l')).toBe('');
  });
  it('reports DB failure after Git publication and rolls back DB writes', async () => {
    raw.exec('CREATE TRIGGER fail_release BEFORE INSERT ON workflow_deploy_history BEGIN SELECT RAISE(ABORT, "failure"); END');
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', requestId: 'request-123' })).rejects.toMatchObject({ code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN', requestId: 'request-123', cause: expect.any(Error) });
    expect(remoteGit('branch', '--list', 'wf/flow')).toContain('wf/flow');
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('does not overwrite a concurrent DB edit after Git publication', async () => {
    const actual = deployment.pushPreparedDeployment;
    vi.spyOn(deployment, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await actual(...args); raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'Concurrent edit' })); return result;
    });
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN', cause: { code: 'BASELINE_STALE' } });
    expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json).title).toBe('Concurrent edit');
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('uses structured nodes over stale content in a structured database spec', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'Structured draft', content: stringify(spec) }));
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(parse(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).title).toBe('Structured draft');
  });
  it('preserves a user-authored content field in the deployed YAML and history', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, content: 'user payload' }));
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(parse(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).content).toBe('user payload');
    const history = raw.prepare('SELECT spec_json FROM workflow_deploy_history WHERE is_active = 1').get() as { spec_json: string };
    expect(parse(JSON.parse(history.spec_json).content).content).toBe('user payload');
  });
  it('preserves even a user-authored content field matching the remaining workflow YAML', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, content: stringify(spec) }));
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(parse(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).content).toBe(stringify(spec));
  });
  it('rejects a legacy null Pack identity that does not match the workflow ID before publishing', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL').run();
    const before = remoteGit('show-ref');
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false }))
      .rejects.toMatchObject({ status: 409, code: 'PACK_ID_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(before);
  });
  // Keep main at the original tree so a default-branch fallback is detectable.
  function legacyWorkflowRef() {
    writeFileSync(path.join(work, 'scripts/run.sh'), '#!/bin/sh\necho legacy-wf\n', { mode: 0o755 });
    writeFileSync(path.join(work, 'only-wf.bin'), Buffer.from([12, 0, 42, 255]));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'legacy workflow');
    const legacy = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
    remoteGit('update-ref', '-d', 'refs/heads/saved/flow');
    return legacy;
  }
  it('rejects a mismatched null Pack identity even when falling back to wf', async () => {
    legacyWorkflowRef();
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL').run();
    const before = remoteGit('show-ref');
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false }))
      .rejects.toMatchObject({ status: 409, code: 'PACK_ID_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(before);
  });
  it('accepts a null Pack identity when the manifest matches the workflow ID', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL').run();
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'flow', version: '1.0.0', workflows: [{ id: 'flow', file: 'workflows/flow.yaml' }] }));
    git(work, 'add', 'workflow.pack.yaml'); git(work, 'commit', '-m', 'matching pack identity');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/flow');
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(result).toMatchObject({ ok: true, deployed: true, packId: 'flow' });
  });
  it('deploys an existing workflow without saved using only its own wf tree', async () => {
    const legacy = legacyWorkflowRef();
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(result).toMatchObject({ candidateCommit: legacy, deployNumber: 1, version: 1 });
    expect(remoteGit('rev-parse', 'refs/heads/main')).toBe(saved);
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{}')).toBe(result.commit);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(parse(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).title).toBe('Database YAML');
    for (const file of ['asset.bin', 'only-wf.bin', 'scripts/run.sh', 'workflow.pack.yaml']) {
      expect(remoteGit('rev-parse', `${result.commit}:${file}`)).toBe(remoteGit('rev-parse', `${legacy}:${file}`));
    }
    expect(remoteGit('ls-tree', result.commit!, 'scripts/run.sh')).toMatch(/^100755 /);
    expect(remoteGit('rev-parse', `${result.commit}^^`)).toBe(legacy);
    expect(raw.prepare('SELECT version, is_active FROM workflow_deploy_history').get()).toEqual({ version: 1, is_active: 1 });
  });
  it('accepts an explicit candidate only when it matches the existing wf ref', async () => {
    const legacy = legacyWorkflowRef();
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', candidateCommit: legacy, autoRun: false });
    expect(result.candidateCommit).toBe(legacy);
  });
  it('rejects a caller-supplied default-branch commit when only wf exists', async () => {
    const legacy = legacyWorkflowRef();
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', candidateCommit: saved })).rejects.toMatchObject({ code: 'CANDIDATE_NOT_SAVED' });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(legacy);
    expect(remoteGit('tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('fails closed when the workflow ref contains a different workflow', async () => {
    legacyWorkflowRef();
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'pack', version: '1.0.0', workflows: [{ id: 'other', file: 'workflows/flow.yaml' }] }));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'different workflow');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
    const invalid = git(work, 'rev-parse', 'HEAD');
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toThrow('Candidate manifest workflow mismatch');
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(invalid);
    expect(remoteGit('tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('prefers saved over a different wf when both refs exist', async () => {
    legacyWorkflowRef();
    remoteGit('update-ref', 'refs/heads/saved/flow', saved);
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(result.candidateCommit).toBe(saved);
    expect(remoteGit('rev-parse', `${result.commit}:scripts/run.sh`)).toBe(remoteGit('rev-parse', `${saved}:scripts/run.sh`));
    expect(remoteGit('ls-tree', result.commit!, 'only-wf.bin')).toBe('');
  });
  it('does not overwrite a concurrent change to the legacy wf ref', async () => {
    legacyWorkflowRef();
    const actual = deployment.pushPreparedDeployment;
    vi.spyOn(deployment, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      remoteGit('update-ref', 'refs/heads/wf/flow', saved);
      return actual(...args);
    });
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved);
    expect(remoteGit('tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('does not overwrite a concurrently created deployment tag or update wf', async () => {
    const legacy = legacyWorkflowRef();
    const actual = deployment.pushPreparedDeployment;
    vi.spyOn(deployment, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      remoteGit('update-ref', 'refs/tags/deploy/flow/#1', saved);
      return actual(...args);
    });
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(legacy);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1')).toBe(saved);
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('requires a saved branch rather than publishing unrelated default content', async () => {
    remoteGit('update-ref', '-d', 'refs/heads/saved/flow');
    remoteGit('update-ref', 'refs/heads/wf/other', saved);
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'SAVE_REQUIRED' });
    expect(remoteGit('tag', '-l')).toBe('');
  });
  it('enforces the wf compare-and-swap when another publisher advances the ref', async () => {
    const actual = deployment.pushPreparedDeployment;
    vi.spyOn(deployment, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      remoteGit('update-ref', 'refs/heads/wf/flow', saved);
      return actual(...args);
    });
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved);
    expect(remoteGit('tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('reports an uncertain push without recording a deployment', async () => {
    vi.spyOn(deployment, 'pushPreparedDeployment').mockRejectedValueOnce(new Error('network lost'));
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
});
