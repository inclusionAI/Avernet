// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { packOperationDdl } from '@avernet/clawweb-shared/server/schema-pack-operations';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { savePackVersionToRemote, reconcilePackOperationFromRemote, readPackOperationFromRemote } from '../pack-version-remote.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import * as gitClient from '../clawflows-git-client.js';
import { PackOperationRepository } from '../../repositories/pack-operation-repository.js';
import { prepareDeployCandidate } from '../auto-heal-deploy-git.js';
import { candidateProjection, operationHash, remoteIdentity } from '../pack-operation-service.js';
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
    { path: 'workflows/flow.yaml', operation: 'replace' as const, contentBase64: Buffer.from(stringify(edited)).toString('base64'), mode: '100644' as const },
    { path: 'scripts/run.sh', operation: 'replace' as const, contentBase64: Buffer.from('echo edited\n').toString('base64'), mode: '100755' as const },
    { path: 'asset.bin', operation: 'create' as const, contentBase64: 'AP8B', mode: '100644' as const },
  ];
  const save = () => savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor',
    requestId: saveId, sourceVersion: 'current', sourceCommit: original, expectedSavedHead: null, changes: changedFiles() });
  const deploy = (candidateCommit?: string, requestId = deployId) => {
    const history = new WorkflowDeployHistoryRepository(db);
    const service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasEditPermission: async () => true, hasExecutePermission: async () => true } as never, config(), author);
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
    for (const ddl of packOperationDdl) raw.exec(ddl);
    raw.prepare('INSERT INTO workflow_specs(workflow_id,pack_id,spec_json,title,owner_id) VALUES (?,?,?,?,?)').run('flow','pack',JSON.stringify(spec),spec.title,'editor');
    db = new SqliteDatabase(raw);
  });
  afterEach(async () => { vi.restoreAllMocks(); if (db) await db.close(); if (root) rmSync(root, { recursive: true, force: true }); });

  it('saves complete content only to saved, then deploys its exact tree and DB snapshot', async () => {
    const saved = await save();
    expect(saved.branch).toBe('saved/flow');
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(original);
    expect(git(root, '--git-dir', remote, 'tag', '--list')).toBe('');
    expect(JSON.parse((await new WorkflowSpecRepository(db).findByWorkflowId('flow'))!.spec_json).title).toBe(edited.title);
    const released = await deploy(saved.commit);
    expect(released).toMatchObject({ ok: true, deployed: true, version: 1, deployNumber: 1, tagName: 'deploy/flow/#1' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow^{tree}')).toBe(git(root, '--git-dir', remote, 'rev-parse', `${saved.commit}^{tree}`));
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/flow/#1^{commit}')).toBe(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow'));
    expect(git(root, '--git-dir', remote, 'show', 'refs/heads/wf/flow:keep.txt')).toBe('untouched');
    expect(git(root, '--git-dir', remote, 'ls-tree', 'refs/heads/wf/flow', 'scripts/run.sh')).toMatch(/^100755/);
    const rows = raw.prepare('SELECT * FROM workflow_deploy_history').all() as Array<{action:string;spec_json:string;is_active:number}>;
    expect(rows).toHaveLength(1); expect(rows[0].action).toBe('deploy'); expect(rows[0].is_active).toBe(1);
    expect(JSON.parse(rows[0].spec_json).title).toBe(edited.title);
  });

  it('same request IDs replay confirmed results without new commits or deployment numbers', async () => {
    const first = await save(); expect(await save()).toEqual(first);
    const published = await deploy(first.commit); expect(await deploy(first.commit)).toEqual(published);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  });

  it('rejects a different payload reusing a save request ID', async () => {
    await save();
    await expect(savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor', requestId: saveId,
      sourceVersion: 'current', sourceCommit: original, expectedSavedHead: null, changes: [] })).rejects.toMatchObject({ status: 409 });
  });

  it('deploys confirmed S even when a later saved S2 exists, never substituting S2', async () => {
    const saved = await save();
    const next = await savePackVersionToRemote({ db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', actorId: 'editor',
      requestId: '33333333-3333-4333-8333-333333333333', sourceVersion: 'draft', sourceCommit: saved.commit, expectedSavedHead: saved.commit,
      changes: [{ path: 'keep.txt', operation: 'replace', contentBase64: Buffer.from('new draft').toString('base64'), mode: '100644' }] });
    await deploy(saved.commit);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/saved/flow')).toBe(next.commit);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow^{tree}')).toBe(git(root, '--git-dir', remote, 'rev-parse', `${saved.commit}^{tree}`));
  });

  it('keeps legacy deployment requests working by capturing saved once', async () => {
    const saved = await save(); await deploy();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow^{tree}')).toBe(git(root, '--git-dir', remote, 'rev-parse', `${saved.commit}^{tree}`));
  });
  const reconcile = (requestId: string) => reconcilePackOperationFromRemote({ db, gitConfig: config(), gitAuthor: author,
    workflowId: 'flow', actorId: 'editor', requestId });

  it('recovers a real successful save after its Git acknowledgement is lost, without creating another candidate', async () => {
    const execute = gitExecution.executeGit;
    let lost = false, pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (...args) => {
      const result = await execute(...args);
      if (args[0][0] === 'push') { pushes++; if (!lost) { lost = true; throw new Error('connection lost'); } }
      return result;
    });
    await expect(save()).rejects.toMatchObject({ code: 'SAVE_OUTCOME_UNKNOWN', requestId: saveId });
    const saved = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/saved/flow');
    expect(JSON.parse((await new WorkflowSpecRepository(db).findByWorkflowId('flow'))!.spec_json).title).toBe(spec.title);
    const receipt = await reconcile(saveId);
    expect(receipt.state).toBe('completed'); expect(receipt.result).toMatchObject({ commit: saved }); expect(pushes).toBe(1);
    expect(await save()).toEqual(receipt.result);
  });

  it('rolls back DB writes on save projection failure and restores them from the same saved candidate', async () => {
    raw.exec("CREATE TRIGGER fail_draft BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'injected DB failure'); END");
    await expect(save()).rejects.toMatchObject({ code: 'SAVE_OUTCOME_UNKNOWN' });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    raw.exec('DROP TRIGGER fail_draft');
    const receipt = await reconcile(saveId);
    expect(receipt.state).toBe('completed');
    expect(JSON.parse((await new WorkflowSpecRepository(db).findByWorkflowId('flow'))!.spec_json).title).toBe(edited.title);
  });

  it('recovers a publish with successful refs but failed DB insert without another tag or number', async () => {
    const saved = await save();
    raw.exec("CREATE TRIGGER fail_release BEFORE INSERT ON workflow_deploy_history BEGIN SELECT RAISE(ABORT, 'injected DB failure'); END");
    await expect(deploy(saved.commit)).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN', requestId: deployId });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(git(root, '--git-dir', remote, 'tag', '--list')).toBe('deploy/flow/#1');
    raw.exec('DROP TRIGGER fail_release');
    expect((await reconcile(deployId)).state).toBe('completed');
    expect(await deploy(saved.commit)).toMatchObject({ deployNumber: 1, version: 1 });
    expect(raw.prepare('SELECT action,is_active FROM workflow_deploy_history').all()).toEqual([{ action: 'deploy', is_active: 1 }]);
  });

  it('keeps recovery payload private and rejects another actor reading an operation', async () => {
    await save();
    const input = { db, gitConfig: config(), gitAuthor: author, workflowId: 'flow', requestId: saveId };
    const receipt = await readPackOperationFromRemote({ ...input, actorId: 'editor' });
    expect(JSON.stringify(receipt)).not.toMatch(/bundleBase64|specJson|baseline|gitConfig/);
    await expect(readPackOperationFromRemote({ ...input, actorId: 'another' })).rejects.toMatchObject({ status: 404 });
  });

  it('does not overwrite a newer DB draft during reconciliation', async () => {
    raw.exec("CREATE TRIGGER fail_draft BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'injected DB failure'); END");
    await expect(save()).rejects.toMatchObject({ code: 'SAVE_OUTCOME_UNKNOWN' }); raw.exec('DROP TRIGGER fail_draft');
    raw.prepare('UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?').run(JSON.stringify({ ...spec, title: 'external newer' }), 'flow');
    await expect(reconcile(saveId)).rejects.toMatchObject({ status: 409 });
    expect(JSON.parse((await new WorkflowSpecRepository(db).findByWorkflowId('flow'))!.spec_json).title).toBe('external newer');
  });

  it('preserves a DB-only draft that existed before accepting a release', async () => {
    const saved = await save();
    raw.prepare('UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?').run(JSON.stringify({ ...spec, title: 'DB-only draft' }), 'flow');
    await deploy(saved.commit);
    expect(JSON.parse((await new WorkflowSpecRepository(db).findByWorkflowId('flow'))!.spec_json).title).toBe('DB-only draft');
    const history = raw.prepare('SELECT spec_json FROM workflow_deploy_history WHERE is_active = 1').get() as {spec_json:string};
    expect(JSON.parse(history.spec_json).title).toBe(edited.title);
  });

  it('retries createOnly with the same request ID after a proven pre-push preparation failure', async () => {
    raw.exec('DELETE FROM workflow_specs');
    const history = new WorkflowDeployHistoryRepository(db);
    const service = new AutoHealSaveService(db, new WorkflowSpecRepository(db), history,
      { hasEditPermission: async () => true } as never, config(), author);
    const create = () => service.save({ workflowId: 'flow', actorId: 'editor', packId: 'pack', spec, createOnly: true, requestId: saveId });
    vi.spyOn(gitClient, 'cloneClawflows').mockRejectedValueOnce(new Error('preparation unavailable'));
    await expect(create()).rejects.toThrow('preparation unavailable');
    expect(raw.prepare('SELECT phase FROM clawweb_pack_operations').get()).toEqual({ phase: 'failed' });
    const saved = await create();
    expect(saved.branch).toBe('saved/flow'); expect(await create()).toEqual(saved);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_specs').get()).toEqual({ n: 1 });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM bot_workflow_permissions').get()).toEqual({ n: 1 });
  });

  it('does not create an operation or leave a workflow when creator permission insertion is ignored', async () => {
    raw.exec('DELETE FROM workflow_specs');
    raw.exec('CREATE TRIGGER ignore_grant BEFORE INSERT ON bot_workflow_permissions BEGIN SELECT RAISE(IGNORE); END');
    const service = new AutoHealSaveService(db, new WorkflowSpecRepository(db), new WorkflowDeployHistoryRepository(db),
      { hasEditPermission: async () => true } as never, config(), author);
    await expect(service.save({ workflowId: 'flow', actorId: 'editor', packId: 'pack', spec, createOnly: true, requestId: saveId })).rejects.toThrow('permission');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_specs').get()).toEqual({ n: 0 });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM clawweb_pack_operations').get()).toEqual({ n: 0 });
  });

  it('does not replay a not-yet-applied remote deployment after its DB baseline changed', async () => {
    const saved = await save();
    const execute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementationOnce(async (...args) => execute(...args));
    const mock = vi.spyOn(await import('../auto-heal-deploy-git.js'), 'pushPreparedDeployment').mockRejectedValueOnce(new Error('transport failed before push'));
    await expect(deploy(saved.commit)).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' }); mock.mockRestore();
    raw.prepare('UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?').run(JSON.stringify({ ...spec, title: 'new baseline' }), 'flow');
    await expect(reconcile(deployId)).rejects.toMatchObject({ status: 409 });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(original);
    expect(git(root, '--git-dir', remote, 'tag', '--list')).toBe('');
  });

  it('fences a stale recovery worker when the same request failed and restarted with another prepared target', async () => {
    const saved = await save();
    const dir = path.join(root, 'recovery-clone'); mkdirSync(dir); await gitClient.cloneClawflows(dir, config());
    const p1 = await prepareDeployCandidate(dir, 'flow', saved.commit, original, 1, 1, author);
    const p2 = await prepareDeployCandidate(dir, 'flow', saved.commit, original, 1, 1, author);
    expect(p1.commit).not.toBe(p2.commit);
    const repo = new PackOperationRepository(db);
    const input = { requestId: deployId, workflowId: 'flow', actorId: 'editor', kind: 'deploy' as const, inputHash: operationHash('fixed-aba-request') };
    const started = await repo.begin(input);
    const payload = (prepared: typeof p1) => JSON.stringify({ git: prepared,
      ...candidateProjection('flow', prepared.content, prepared.manifestContent), packId: 'pack', remoteHash: remoteIdentity(config()),
      version: 1, note: null, preserveDraft: false });
    await repo.prepare(deployId, payload(p1), started.operation.attempt);
    const originalClaim = PackOperationRepository.prototype.markPushing;
    vi.spyOn(PackOperationRepository.prototype, 'markPushing').mockImplementationOnce(async function (requestId, expectedAttempt) {
      await repo.failBeforePush(requestId, JSON.stringify({ code: 'CANCELLED_BEFORE_PUSH' }), expectedAttempt);
      const restarted = await repo.restartFailed(input, expectedAttempt);
      await repo.prepare(requestId, payload(p2), restarted.operation.attempt);
      return originalClaim.call(this, requestId, expectedAttempt);
    });
    await expect(reconcile(deployId)).rejects.toMatchObject({ status: 503 });
    const pending = (await repo.get(deployId))!;
    expect(pending.attempt).toBe(2); expect(pending.phase).toBe('prepared'); expect(pending.resultJson).toBeNull();
    expect(JSON.parse(pending.payloadJson!).git.commit).toBe(p2.commit);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(original);
    expect(git(root, '--git-dir', remote, 'tag', '--list')).toBe('');
    const recovered = await reconcile(deployId);
    expect(recovered.state).toBe('completed'); expect(recovered.result.commit).toBe(p2.commit);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(p2.commit);
  });

});
