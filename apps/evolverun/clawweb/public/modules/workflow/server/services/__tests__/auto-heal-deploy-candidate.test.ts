// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { packOperationDdl } from '@avernet/clawweb-shared/server/schema-pack-operations';
import { sqliteDialect } from '@avernet/clawweb-shared/server/db/dialect';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { stringify } from 'yaml';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { reconcilePackOperation } from '../pack-operation-service.js';
import { cloneClawflows } from '../clawflows-git-client.js';
import * as deployGit from '../auto-heal-deploy-git.js';
import { inspectDeployCandidate, pushDeployCandidate } from '../auto-heal-deploy-git.js';

vi.setConfig({ testTimeout: 45_000, hookTimeout: 30_000 });

const spec = { id: 'wf-test', title: 'Candidate', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
const author = { name: 'Tester', email: 'test@example.com' };
const git = (dir: string, ...args: string[]) => execFileSync('git', args, { cwd: dir, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();

describe('exact saved candidate deployment', () => {
  let root: string, remote: string, work: string, candidate: string;
  let raw: Database.Database, db: SqliteDatabase, service: AutoHealDeployService;
  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'candidate-deploy-'));
    remote = path.join(root, 'remote.git'); work = path.join(root, 'work');
    git(root, 'init', '--bare', remote); git(root, 'init', work);
    git(work, 'config', 'user.name', author.name); git(work, 'config', 'user.email', author.email);
    mkdirSync(path.join(work, 'workflows')); mkdirSync(path.join(work, 'scripts'));
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'pack-1', version: '1.0.0', workflows: [{ id: 'wf-test', file: 'workflows/wf-test.yaml' }] }));
    writeFileSync(path.join(work, 'workflows/wf-test.yaml'), stringify(spec));
    writeFileSync(path.join(work, 'scripts/run.sh'), 'saved script\n');
    writeFileSync(path.join(work, 'asset.txt'), 'saved asset\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'candidate'); candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'remote', 'add', 'origin', remote); git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    git(work, 'push', 'origin', 'HEAD:refs/heads/main');
    git(root, '--git-dir', remote, 'symbolic-ref', 'HEAD', 'refs/heads/main');
    raw = new Database(path.join(root, 'workflow.sqlite'));
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0, title TEXT, version INTEGER, owner_id TEXT);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
    for (const ddl of packOperationDdl) raw.exec(sqliteDialect.renderDdl(ddl));
    raw.prepare('INSERT INTO workflow_specs (workflow_id, pack_id, spec_json) VALUES (?, ?, ?)').run('wf-test', 'pack-1', JSON.stringify(spec));
    db = new SqliteDatabase(raw);
    const history = new WorkflowDeployHistoryRepository(db);
    service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true }); });
  const unknown = () => expect(raw.prepare("SELECT phase, lock_workflow_id FROM clawweb_pack_operations WHERE phase = 'unknown'").all())
    .toEqual([{ phase: 'unknown', lock_workflow_id: (raw.prepare('SELECT id FROM workflow_specs WHERE workflow_id = ?').get('wf-test') as { id: number }).id }]);
  const deploy = () => service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false });

  it('publishes precisely the saved tree, including scripts and assets, and binds history and tag', async () => {
    writeFileSync(path.join(work, 'scripts/run.sh'), 'old deployed script\n');
    writeFileSync(path.join(work, 'old-only.txt'), 'must disappear\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'old deployment');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const result = await deploy();
    const branch = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    expect(git(root, '--git-dir', remote, 'rev-parse', `${branch}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.tagName}^{commit}`)).toBe(branch);
    expect(git(root, '--git-dir', remote, 'show', `${branch}:scripts/run.sh`)).toBe('saved script');
    expect(git(root, '--git-dir', remote, 'show', `${branch}:asset.txt`)).toBe('saved asset');
    expect(git(root, '--git-dir', remote, 'ls-tree', '--name-only', branch)).not.toContain('old-only.txt');
    expect(git(root, '--git-dir', remote, 'cat-file', '-p', `refs/tags/${result.tagName}`)).toContain(`candidateCommit=${candidate}`);
    const row = raw.prepare('SELECT * FROM workflow_deploy_history').get() as { spec_json: string; is_active: number; action: string };
    expect(row.action).toBe('deploy'); expect(row.is_active).toBe(1);
    expect(JSON.parse(row.spec_json).content).toBe(stringify(spec));
  });
  const saveFacadeCandidate = () => {
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'pack-1', version: '1.0.0',
      workflows: [{ id: 'wf-test', file: 'workflows/wf-test.yaml' }],
      facades: [{ command: 'main-command', aliases: ['alias-command'], defaultWorkflow: 'wf-test' }] }));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'candidate with facades');
    candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
  };

  it.each(['main-command', 'alias-command'])('rejects an existing foreign owner for %s before Git push', async command => {
    saveFacadeCandidate();
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const previous = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    raw.prepare('INSERT INTO facade_bindings (command, workflow_id, pack_id) VALUES (?, ?, ?)').run(command, 'other', 'other-pack');
    const push = vi.spyOn(deployGit, 'pushPreparedDeployment');
    await expect(deploy()).rejects.toMatchObject({ status: 409, code: 'FACADE_CONFLICT' });
    expect(push).not.toHaveBeenCalled();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(previous);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT command, workflow_id, pack_id FROM facade_bindings').all()).toEqual([{ command, workflow_id: 'other', pack_id: 'other-pack' }]);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(raw.prepare('SELECT phase, lock_workflow_id FROM clawweb_pack_operations').all()).toEqual([{ phase: 'failed', lock_workflow_id: null }]);
  });

  it('permits existing same-workflow facade ownership and publishes both commands', async () => {
    saveFacadeCandidate();
    raw.prepare('INSERT INTO facade_bindings (command, workflow_id, pack_id) VALUES (?, ?, ?)').run('main-command', 'wf-test', 'old-pack');
    const result = await deploy();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(result.commit);
    expect(raw.prepare('SELECT command, workflow_id, pack_id FROM facade_bindings ORDER BY command').all()).toEqual([
      { command: 'alias-command', workflow_id: 'wf-test', pack_id: 'pack-1' },
      { command: 'main-command', workflow_id: 'wf-test', pack_id: 'pack-1' },
    ]);
  });

  it('fails closed for absent saved or a candidate with no saved provenance', async () => {
    git(work, 'push', 'origin', ':refs/heads/saved/wf-test');
    await expect(service.deploy({ workflowId: 'wf-test', actorId: 'actor' })).rejects.toMatchObject({ status: 409, code: 'SAVE_REQUIRED' });
    git(work, 'push', 'origin', `${candidate}:refs/heads/saved/wf-test`);
    await expect(service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: 'a'.repeat(40) }))
      .rejects.toMatchObject({ status: 409, code: 'CANDIDATE_NOT_SAVED' });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(raw.prepare('SELECT phase FROM clawweb_pack_operations').all()).toEqual([{ phase: 'failed' }, { phase: 'failed' }]);
  });
  it('captures saved once for a legacy omitted candidate without requiring DB equality or overwriting a different draft', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'stale DB' }));
    const result = await service.deploy({ workflowId: 'wf-test', actorId: 'actor', autoRun: false });
    expect(result.candidateCommit).toBe(candidate);
    const row = raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string };
    expect(JSON.parse(row.spec_json).title).toBe('stale DB');
    const published = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(published.spec_json)).toMatchObject({ title: 'Candidate', content: stringify(spec) });
  });
  it('reports a Git-success DB-failure without activating history', async () => {
    raw.exec(`CREATE TRIGGER fail_activation BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END`);
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBeTruthy();
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([]);
    unknown();
  });
  it('rejects a stale deployment branch lease without creating its tag', async () => {
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const inspected = await inspectDeployCandidate(clone, 'wf-test', 'pack-1', candidate);
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    await expect(pushDeployCandidate(clone, 'wf-test', candidate, inspected.previousCommit, 1, 1, author)).rejects.toThrow();
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(candidate);
  });
  it('publishes the inspected S even when saved advances before promotion', async () => {
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const inspected = await inspectDeployCandidate(clone, 'wf-test', 'pack-1', candidate);
    writeFileSync(path.join(work, 'asset.txt'), 'newer save');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'newer save');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    const published = await pushDeployCandidate(clone, 'wf-test', candidate, inspected.previousCommit, 1, 1, author);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${published.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/saved/wf-test')).toBe(git(work, 'rev-parse', 'HEAD'));
  });
  it('does not update the branch when a tag is concurrently created', async () => {
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const inspected = await inspectDeployCandidate(clone, 'wf-test', 'pack-1', candidate);
    git(work, 'tag', '-a', 'deploy/wf-test/#1', '-m', 'another deployment');
    git(work, 'push', 'origin', 'refs/tags/deploy/wf-test/#1');
    await expect(pushDeployCandidate(clone, 'wf-test', candidate, inspected.previousCommit, 1, 1, author)).rejects.toThrow('outcome unknown');
    expect(git(root, '--git-dir', remote, 'branch', '--list', 'wf/wf-test')).toBe('');
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(candidate);
  });

  it('does not activate when the DB changes after the Git push', async () => {
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await realPush(...args);
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'newer' }));
      return result;
    });
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([]);
    unknown();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBeTruthy();
  });

  it('redeploys the same candidate as a fresh leased commit and new tag', async () => {
    const first = await deploy();
    const firstCommit = git(root, '--git-dir', remote, 'rev-parse', `refs/tags/${first.tagName}^{commit}`);
    const second = await deploy();
    const secondCommit = git(root, '--git-dir', remote, 'rev-parse', `refs/tags/${second.tagName}^{commit}`);
    expect(firstCommit).not.toBe(secondCommit);
    expect(second.deployNumber).toBe(2);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${secondCommit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(secondCommit);
  });

  it('fences a second same-service deployment while the first is between Git and DB', async () => {
    let resume!: () => void;
    let pushed!: () => void;
    const paused = new Promise<void>(resolve => { resume = resolve; });
    const atPush = new Promise<void>(resolve => { pushed = resolve; });
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await realPush(...args);
      pushed();
      await paused;
      return result;
    });
    const otherDb = new SqliteDatabase(new Database(path.join(root, 'workflow.sqlite')));
    const otherHistory = new WorkflowDeployHistoryRepository(otherDb);
    const otherService = new AutoHealDeployService(otherDb, new WorkflowSpecRepository(otherDb, otherHistory), otherHistory,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    const first = deploy();
    try {
      await atPush;
      await expect(otherService.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false }))
        .rejects.toMatchObject({ status: 409, code: 'WORKFLOW_BUSY' });
    } finally { resume(); await otherDb.close(); }
    const result = await first;
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([{ action: 'deploy', is_active: 1 }]);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test'))
      .toBe(git(root, '--git-dir', remote, 'rev-parse', `refs/tags/${result.tagName}^{commit}`));
  });
  it('does not overwrite an active history change made after reservation', async () => {
    const insert = raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('pack-1', 'wf-test', ?, ?, ?, 'deploy', '{}', ?)`);
    insert.run(8, 8, 'old', 1); insert.run(9, 9, 'concurrent', 0);
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await realPush(...args);
      expect(await new WorkflowDeployHistoryRepository(db).setActive('wf-test', 9)).toBe(true);
      return result;
    });
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(raw.prepare('SELECT deploy_number FROM workflow_deploy_history WHERE is_active = 1').all()).toEqual([{ deploy_number: 9 }]);
    expect(raw.prepare("SELECT action, is_active FROM workflow_deploy_history WHERE action = 'pending'").all()).toEqual([]);
    unknown();
  });
  it('keeps an ambiguous private operation as a durable fence on later attempts', async () => {
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockRejectedValueOnce(new Error('push acknowledgement lost'));
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN' });
    await expect(deploy()).rejects.toMatchObject({ status: 409, code: 'WORKFLOW_BUSY' });
    expect(raw.prepare('SELECT count(*) AS count FROM workflow_deploy_history').get()).toEqual({ count: 0 });
    unknown();
  });
  it('retains a private unknown fence when a real successful push loses its acknowledgement', async () => {
    const realGit = gitExecution.executeGit;
    let pushCompleted = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await realGit(args, options);
      if (args[0] === 'push' && args.includes('--atomic')) {
        pushCompleted = true;
        throw new Error('simulated transport loss after remote accepted push');
      }
      return result;
    });
    const rerun = vi.fn(async () => 'must-not-run');
    const history = new WorkflowDeployHistoryRepository(db);
    const withRerun = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, rerun);
    const attempt = () => withRerun.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: true });
    await expect(attempt()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(pushCompleted).toBe(true);
    const remoteBranch = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(remoteBranch);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${remoteBranch}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([]);
    unknown();
    expect(rerun).not.toHaveBeenCalled();
    await expect(attempt()).rejects.toMatchObject({ status: 409, code: 'WORKFLOW_BUSY' });
  });
  it('allocates past existing Git tags without adding any public reservation', async () => {
    git(work, 'tag', '-a', 'deploy/wf-test/#5', '-m', 'external');
    git(work, 'push', 'origin', 'refs/tags/deploy/wf-test/#5');
    const result = await deploy();
    expect(result.deployNumber).toBe(6);
    expect(raw.prepare('SELECT deploy_number, action, is_active FROM workflow_deploy_history').all())
      .toEqual([{ deploy_number: 6, action: 'deploy', is_active: 1 }]);
  });

  it('keeps S immutable when a concurrent saved update lands immediately before push', async () => {
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const inspected = await inspectDeployCandidate(clone, 'wf-test', 'pack-1', candidate);
    writeFileSync(path.join(work, 'asset.txt'), 'saved concurrently');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'concurrent save');
    const newer = git(work, 'rev-parse', 'HEAD');
    const realGit = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
      return realGit(args, options);
    });
    // saved is not a deployment lease: confirming S must never silently deploy S2.
    const published = await pushDeployCandidate(clone, 'wf-test', candidate, inspected.previousCommit, 1, 1, author);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/saved/wf-test')).toBe(newer);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${published.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
  });

  it('publishes exact Git-only edited workflow YAML without overwriting an unrelated DB draft', async () => {
    const content = stringify({ ...spec, title: 'Edited candidate' });
    writeFileSync(path.join(work, 'workflows/wf-test.yaml'), content);
    git(work, 'add', '.'); git(work, 'commit', '-m', 'edit candidate YAML only');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    candidate = git(work, 'rev-parse', 'HEAD');
    const result = await deploy();
    const row = raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string };
    expect(JSON.parse(row.spec_json).title).toBe('Candidate');
    const published = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(published.spec_json)).toMatchObject({ title: 'Edited candidate', content });
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([{ action: 'deploy', is_active: 1 }]);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(result.commit);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
  });

  it('deploys a previously journaled S after saved advances to S2, preserving the newer draft', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const saver = new AutoHealSaveService(db, new WorkflowSpecRepository(db, history), history,
      { hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    const first = await saver.save({ workflowId: 'wf-test', actorId: 'actor', spec: { ...spec, title: 'Saved S' }, requestId: 'save-first-0001' });
    const second = await saver.save({ workflowId: 'wf-test', actorId: 'actor', spec: { ...spec, title: 'Saved S2' }, requestId: 'save-next-0002' });
    candidate = first.commit;
    const result = await deploy();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/saved/wf-test')).toBe(second.commit);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(root, '--git-dir', remote, 'rev-parse', `${first.commit}^{tree}`));
    const draft = raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string };
    const published = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(draft.spec_json).title).toBe('Saved S2');
    expect(JSON.parse(published.spec_json).title).toBe('Saved S');
  });

  it('reconciles a lost acknowledgement with the same request, release objects and deploy number', async () => {
    const requestId = 'deploy-recovery-0001';
    const realGit = gitExecution.executeGit; let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await realGit(args, options);
      if (args[0] === 'push') { pushes++; throw new Error('acknowledgement lost'); }
      return result;
    });
    const attempt = () => service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false, requestId });
    await expect(attempt()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN', requestId });
    const commit = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    const receipt = await reconcilePackOperation(db, { remoteUrl: remote, username: author.name, email: author.email }, requestId, 'wf-test', 'actor');
    expect(receipt).toMatchObject({ requestId, state: 'completed', result: { commit, deployNumber: 1 } });
    expect((await attempt()).commit).toBe(commit);
    expect(pushes).toBe(1);
    expect(raw.prepare('SELECT action, is_active, deploy_number FROM workflow_deploy_history').all())
      .toEqual([{ action: 'deploy', is_active: 1, deploy_number: 1 }]);
    expect(raw.prepare('SELECT phase, lock_workflow_id FROM clawweb_pack_operations').all())
      .toEqual([{ phase: 'completed', lock_workflow_id: null }]);
  });
});
