// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { savePackVersionToRemote, reconcilePackOperationFromRemote, readPackOperationFromRemote } from '../pack-version-remote.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';

const spec = { id: 'flow', title: 'Original', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const author = { name: 'Fixture', email: 'fixture@example.invalid' };
const saveId = '11111111-1111-4111-8111-111111111111';
const deployId = '22222222-2222-4222-8222-222222222222';
const edited = { ...spec, title: 'Edited YAML' };

describe('ClawWeb complete Pack save and deployment lifecycle', () => {
  let root: string, remote: string, work: string, original: string;
  let raw: Database.Database, db: SqliteDatabase;
  const config = () => ({ remoteUrl: remote, username: author.name, email: author.email, token: '' });
  const changedFiles = () => [
    { path: 'scripts/run.sh', operation: 'replace' as const, contentBase64: Buffer.from('echo edited\n').toString('base64'), mode: '100755' as const },
    { path: 'asset.bin', operation: 'create' as const, contentBase64: 'AP8B', mode: '100644' as const },
  ];
  const save = (sourceCommit = original) => savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor',
    requestId: saveId, sourceVersion: 'draft', sourceCommit, expectedSavedHead: sourceCommit, changes: changedFiles() });
  const deploy = (candidateCommit?: string, requestId = deployId) => {
    const history = new WorkflowDeployHistoryRepository(db);
    const service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      new BotWorkflowPermissionRepository(db), config(), author);
    return service.deploy({ workflowId: 'flow', actorId: 'editor', candidateCommit, requestId, autoRun: false });
  };
  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'pack-lifecycle-')); remote = path.join(root, 'remote.git'); work = path.join(root, 'work');
    git(root, 'init', '--bare', remote); git(root, 'init', work);
    git(work, 'config', 'user.name', author.name); git(work, 'config', 'user.email', author.email);
    mkdirSync(path.join(work, 'workflows')); mkdirSync(path.join(work, 'scripts'));
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'pack', version: '1.0.0', workflows: [{ id: 'flow', file: 'workflows/flow.yaml' }] }));
    writeFileSync(path.join(work, 'workflows/flow.yaml'), stringify(spec)); writeFileSync(path.join(work, 'scripts/run.sh'), 'echo original\n');
    writeFileSync(path.join(work, 'keep.txt'), 'untouched\n'); git(work, 'add', '.'); git(work, 'commit', '-m', 'original');
    original = git(work, 'rev-parse', 'HEAD'); git(work, 'remote', 'add', 'origin', remote);
    git(work, 'push', 'origin', 'HEAD:refs/heads/main', 'HEAD:refs/heads/wf/flow'); git(root, '--git-dir', remote, 'symbolic-ref', 'HEAD', 'refs/heads/main');
    raw = new Database(path.join(root, 'workflow.sqlite'));
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER, UNIQUE(pack_id,workflow_id,deploy_number,version));
      CREATE TABLE cm_app_config (id INTEGER PRIMARY KEY,config_key TEXT,config_yaml TEXT,version INTEGER,enabled INTEGER,description TEXT,updated_by TEXT,gmt_create INTEGER,gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY,bot_id TEXT,bot_owner_id TEXT,workflow_id TEXT,env TEXT,can_view INTEGER,can_execute INTEGER,can_edit INTEGER,gmt_create INTEGER,gmt_modified INTEGER);
      CREATE TABLE facade_bindings (command TEXT PRIMARY KEY,workflow_id TEXT,pack_id TEXT,remark TEXT,gmt_create INTEGER,gmt_modified INTEGER);`);
    raw.prepare('INSERT INTO workflow_specs(workflow_id,pack_id,spec_json,title,owner_id) VALUES (?,?,?,?,?)').run('flow','pack',JSON.stringify(spec),spec.title,'editor');
    raw.prepare('INSERT INTO bot_workflow_permissions(bot_id,bot_owner_id,workflow_id,env,can_view,can_execute,can_edit) VALUES (NULL,?,?,?,?,?,?)')
      .run('editor', 'flow', 'test', 1, 1, 1);
    db = new SqliteDatabase(raw);
  });
  afterEach(async () => { vi.restoreAllMocks(); if (db) await db.close(); if (root) rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  const saveWorkflow = (actor = 'editor') => {
    const history = new WorkflowDeployHistoryRepository(db);
    return new AutoHealSaveService(db, new WorkflowSpecRepository(db, history), history,
      new BotWorkflowPermissionRepository(db), config(), author)
      .save({ workflowId: 'flow', actorId: actor, spec: edited, requestId: saveId });
  };
  const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
  const draftTitle = async () => JSON.parse((await new WorkflowSpecRepository(db).findByWorkflowId('flow'))!.spec_json).title;

  it('saves workflow and Pack files to wf, then tags their exact saved commit', async () => {
    const workflowSaved = await saveWorkflow();
    expect(workflowSaved).toMatchObject({ ok: true, saved: true, workflowId: 'flow', packId: 'pack', branch: 'wf/flow' });
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    const saved = await save(workflowSaved.commit!);
    expect(saved.branch).toBe('wf/flow');
    expect(remoteGit('show', `${saved.commit}:workflows/flow.yaml`)).toBe(stringify(edited).trim());
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved.commit);
    expect(remoteGit('tag', '--list')).toBe('');
    const released = await deploy(saved.commit);
    expect(released).toMatchObject({ ok: true, deployed: true, version: 1, deployNumber: 1, tagName: 'deploy/flow/#1' });
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{commit}')).toBe(released.commit);
    expect(released.commit).toBe(saved.commit);
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved.commit);
    expect(remoteGit('show', `${released.commit}:workflows/flow.yaml`)).toBe(stringify(edited).trim());
    expect(remoteGit('show', `${released.commit}:keep.txt`)).toBe('untouched');
    expect(remoteGit('rev-parse', `${released.commit}:asset.bin`)).toBe(remoteGit('rev-parse', `${saved.commit}:asset.bin`));
    expect(remoteGit('ls-tree', released.commit!, 'scripts/run.sh')).toMatch(/^100755/);
    const rows = raw.prepare('SELECT action,spec_json,is_active FROM workflow_deploy_history').all() as Array<{action:string;spec_json:string;is_active:number}>;
    expect(rows).toHaveLength(1); expect(rows[0]).toMatchObject({ action: 'deploy', is_active: 1 });
    expect(JSON.parse(rows[0].spec_json).title).toBe(edited.title);
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE name = 'clawweb_pack_operations'").get()).toBeUndefined();
  });

  it('rejects workflow YAML changes through Pack-file save', async () => {
    await expect(savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor',
      sourceVersion: 'draft', sourceCommit: original, expectedSavedHead: original,
      changes: [{ path: 'workflows/flow.yaml', operation: 'replace', contentBase64: Buffer.from(stringify(edited)).toString('base64'), mode: '100644' }] }))
      .rejects.toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(await draftTitle()).toBe(spec.title);
  });

  it('denies an unauthorized workflow save without changing DB or Git', async () => {
    await expect(saveWorkflow('intruder')).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    expect(await draftTitle()).toBe(spec.title);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
  });

  it.each(['../escape', '.git/config', 'scripts/../../escape'])('rejects unsafe path %s before pushing', async file => {
    await expect(savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor',
      sourceVersion: 'draft', sourceCommit: original, expectedSavedHead: original,
      changes: [{ path: file, operation: 'create', contentBase64: 'YQ==', mode: '100644' }] })).rejects.toThrow();
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(original);
  });

  it('retains a completed DB draft when the separate Pack save conflicts', async () => {
    const workflowSaved = await saveWorkflow();
    await expect(save()).rejects.toMatchObject({ status: 409, code: 'VERSION_CONFLICT' });
    expect(await draftTitle()).toBe(edited.title);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(workflowSaved.commit);
    expect(remoteGit('tag', '--list')).toBe('');
  });

  it('does not treat a repeated request ID as an idempotent replay', async () => {
    const first = await save();
    await expect(save()).rejects.toMatchObject({ status: 409, code: 'VERSION_CONFLICT' });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(first.commit);
    const one = await deploy(first.commit); const two = await deploy(first.commit);
    expect(two.deployNumber).toBe(one.deployNumber + 1);
    expect(two.commit).toBe(one.commit); expect(two.tagName).not.toBe(one.tagName);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 2 });
  });

  it('rejects an older candidate after a later saved head exists', async () => {
    const first = await save();
    const next = await savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor',
      requestId: '33333333-3333-4333-8333-333333333333', sourceVersion: 'draft', sourceCommit: first.commit, expectedSavedHead: first.commit,
      changes: [{ path: 'keep.txt', operation: 'replace', contentBase64: Buffer.from('new draft').toString('base64'), mode: '100644' }] });
    await expect(deploy(first.commit)).rejects.toMatchObject({ status: 409, code: 'CANDIDATE_NOT_SAVED' });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(next.commit);
    expect(remoteGit('tag', '--list')).toBe('');
  });

  it('exposes a real saved ref after a lost acknowledgement without claiming recovery', async () => {
    const execute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (...args) => {
      const result = await execute(...args);
      if (args[0][0] === 'push') throw new Error('connection lost after push');
      return result;
    });
    await expect(save()).rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError', branch: 'wf/flow', commit: expect.any(String) });
    const actual = remoteGit('rev-parse', 'refs/heads/wf/flow');
    expect(actual).not.toBe(original);
    expect(remoteGit('show', `${actual}:scripts/run.sh`)).toBe('echo edited');
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(actual);
    await expect(save()).rejects.toMatchObject({ status: 409, code: 'VERSION_CONFLICT' });
    const input = { db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor', requestId: saveId };
    await expect(readPackOperationFromRemote(input)).rejects.toMatchObject({ status: 503, code: 'OPERATION_RECOVERY_UNAVAILABLE' });
    await expect(reconcilePackOperationFromRemote(input)).rejects.toMatchObject({ status: 503, code: 'OPERATION_RECOVERY_UNAVAILABLE' });
  });

  it('reports Git-success DB-failure without claiming activation or rolling back the remote', async () => {
    const workflowSaved = await saveWorkflow(); const saved = await save(workflowSaved.commit!);
    raw.exec("CREATE TRIGGER fail_release BEFORE INSERT ON workflow_deploy_history BEGIN SELECT RAISE(ABORT, 'injected DB failure'); END");
    await expect(deploy(saved.commit)).rejects.toMatchObject({ code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN', requestId: deployId, cause: expect.any(Error) });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(remoteGit('tag', '--list')).toBe('deploy/flow/#1');
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{commit}')).toBe(remoteGit('rev-parse', 'refs/heads/wf/flow'));
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved.commit);
    expect(await draftTitle()).toBe(edited.title);
  });

  it('rolls back a failed database save without creating a Git candidate', async () => {
    raw.exec("CREATE TRIGGER fail_draft BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'injected DB failure'); END");
    await expect(saveWorkflow()).rejects.toThrow('injected DB failure');
    expect(await draftTitle()).toBe(spec.title);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(remoteGit('tag', '--list')).toBe('');
  });
});
