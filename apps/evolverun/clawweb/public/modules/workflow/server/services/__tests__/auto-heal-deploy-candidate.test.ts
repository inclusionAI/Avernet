// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { stringify } from 'yaml';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
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

describe('database workflow and saved Pack deployment', () => {
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
    raw.prepare('INSERT INTO workflow_specs (workflow_id, pack_id, spec_json) VALUES (?, ?, ?)').run('wf-test', 'pack-1', JSON.stringify(spec));
    db = new SqliteDatabase(raw);
    const history = new WorkflowDeployHistoryRepository(db);
    service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  const deploy = () => service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false });

  it('publishes the overlaid tree, including scripts and assets, and binds history and tag', async () => {
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
    const overlaid = git(root, '--git-dir', remote, 'rev-parse', `${branch}^`);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${overlaid}^`)).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'cat-file', '-p', `refs/tags/${result.tagName}`)).toContain(`candidateCommit=${overlaid}`);
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
  });
  it('overlays the database draft on saved when the candidate is omitted', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'stale DB' }));
    const result = await service.deploy({ workflowId: 'wf-test', actorId: 'actor', autoRun: false });
    expect(result.candidateCommit).toBe(candidate);
    const row = raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string };
    expect(JSON.parse(row.spec_json).title).toBe('stale DB');
    const published = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(published.spec_json).title).toBe('stale DB');
  });
  it('reports a Git-success DB-failure without activating history', async () => {
    raw.exec(`CREATE TRIGGER fail_activation BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END`);
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBeTruthy();
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([]);
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
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([]);
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

  it('does not overwrite an active history change made after reading the baseline', async () => {
    const insert = raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('pack-1', 'wf-test', ?, ?, ?, 'deploy', '{}', ?)`);
    insert.run(8, 8, 'old', 1); insert.run(9, 9, 'concurrent', 0);
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await realPush(...args);
      expect(await new WorkflowDeployHistoryRepository(db).setActive('wf-test', 9)).toBe(true);
      return result;
    });
    await expect(deploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(raw.prepare('SELECT deploy_number FROM workflow_deploy_history WHERE is_active = 1').all()).toEqual([{ deploy_number: 9 }]);
    expect(raw.prepare("SELECT action, is_active FROM workflow_deploy_history WHERE action = 'pending'").all()).toEqual([]);
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

  it('overlays the DB draft instead of trusting Git-only workflow YAML', async () => {
    const content = stringify({ ...spec, title: 'Edited candidate' });
    writeFileSync(path.join(work, 'workflows/wf-test.yaml'), content);
    git(work, 'add', '.'); git(work, 'commit', '-m', 'edit candidate YAML only');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    candidate = git(work, 'rev-parse', 'HEAD');
    const result = await deploy();
    const row = raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string };
    expect(JSON.parse(row.spec_json).title).toBe('Candidate');
    const published = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(published.spec_json)).toMatchObject({ title: 'Candidate', content: stringify(spec) });
    expect(raw.prepare('SELECT action, is_active FROM workflow_deploy_history').all()).toEqual([{ action: 'deploy', is_active: 1 }]);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(result.commit);
    expect(git(root, '--git-dir', remote, 'show', `${result.commit}:workflows/wf-test.yaml`)).toBe(stringify(spec).trim());
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}:asset.txt`)).toBe(git(work, 'rev-parse', `${candidate}:asset.txt`));
  });


  it('reports a lost deployment acknowledgement after a real atomic push without running or recording a release', async () => {
    const execute = gitExecution.executeGit;
    let pushed = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await execute(args, options);
      if (args[0] === 'push' && args.includes('--atomic')) { pushed = true; throw new Error('lost acknowledgement'); }
      return result;
    });
    const run = vi.fn(async () => 'not-run');
    const history = new WorkflowDeployHistoryRepository(db);
    const withRun = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, run);
    await expect(withRun.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: true, requestId: 'deploy-unknown-001' }))
      .rejects.toMatchObject({ status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN', requestId: 'deploy-unknown-001', tagName: 'deploy/wf-test/#1' });
    expect(pushed).toBe(true);
    const branch = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(branch);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${branch}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(run).not.toHaveBeenCalled();
  });

  it('uses the selected saved Pack when its ref advances immediately before deployment push', async () => {
    const push = deployGit.pushPreparedDeployment;
    writeFileSync(path.join(work, 'asset.txt'), 'later saved asset');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'later save');
    const newer = git(work, 'rev-parse', 'HEAD');
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
      return push(...args);
    });
    const result = await deploy();
    expect(result.candidateCommit).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/saved/wf-test')).toBe(newer);
    expect(git(root, '--git-dir', remote, 'show', `${result.commit}:asset.txt`)).toBe('saved asset');
    expect(git(root, '--git-dir', remote, 'show', `${newer}:asset.txt`)).toBe('later saved asset');
  });

});
