// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
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
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, async () => true);
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  const deploy = () => service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false });

  const token = () => {
    const row = raw.prepare('SELECT * FROM workflow_specs WHERE workflow_id = ?').get('wf-test') as Record<string, unknown>;
    return createHash('sha256').update(JSON.stringify(['editor-draft-current-value-v1', row.id, row.workflow_id, row.pack_id, row.spec_json,
      row.version, row.title, row.owner_id, row.gmt_create, row.gmt_modified])).digest('hex');
  };
  const editorDeploy = () => service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false, expectedDraftToken: token() });

  it.each([true, false])('rejects missing wf with active history before push (saved present: %s)', async savedPresent => {
    writeFileSync(path.join(work, 'asset.txt'), 'newer published asset');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'newer deployment');
    const published = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    git(work, 'tag', '-a', 'deploy/wf-test/#1', '-m', 'published');
    git(work, 'push', 'origin', 'refs/tags/deploy/wf-test/#1');
    git(work, 'push', 'origin', ':refs/heads/wf/wf-test');
    if (!savedPresent) git(work, 'push', 'origin', ':refs/heads/saved/wf-test');
    raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('pack-1', 'wf-test', 1, 1, 'deploy/wf-test/#1', 'deploy', '{}', 1)`).run();
    const refs = git(root, '--git-dir', remote, 'show-ref', '--heads', '--tags');
    const history = raw.prepare('SELECT * FROM workflow_deploy_history').all();
    const specs = raw.prepare('SELECT * FROM workflow_specs').all();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(published);
    const push = vi.spyOn(deployGit, 'pushPreparedDeployment');
    const requestId = 'missing-active-wf-001';
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', autoRun: false, requestId, expectedDraftToken: token() }))
      .rejects.toMatchObject({ status: 409, code: 'DEPLOY_BASELINE_MISSING', requestId });
    expect(push).not.toHaveBeenCalled();
    expect(git(root, '--git-dir', remote, 'show-ref', '--heads', '--tags')).toBe(refs);
    expect(raw.prepare('SELECT * FROM workflow_deploy_history').all()).toEqual(history);
    expect(raw.prepare('SELECT * FROM workflow_specs').all()).toEqual(specs);
    if (savedPresent) {
      const result = await deploy();
      expect(git(root, '--git-dir', remote, 'show', `${result.commit}:asset.txt`)).toBe('saved asset');
      expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(published);
    } else {
      await expect(service.deploy({ workflowId: 'wf-test', actorId: 'actor' })).rejects.toMatchObject({ status: 409, code: 'SAVE_REQUIRED' });
    }
  });

  it('allows editor first deployment when wf and active history are absent', async () => {
    raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('other-pack', 'other-workflow', 1, 1, 'other-tag', 'deploy', '{}', 1)`).run();
    expect(raw.prepare("SELECT * FROM workflow_deploy_history WHERE workflow_id = 'wf-test' AND is_active = 1").all()).toEqual([]);
    expect(git(root, '--git-dir', remote, 'branch', '--list', 'wf/wf-test')).toBe('');
    const result = await editorDeploy();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(result.commit);
    expect(git(root, '--git-dir', remote, 'show', 'refs/heads/wf/wf-test:scripts/run.sh')).toBe('saved script');
    expect(git(root, '--git-dir', remote, 'show', 'refs/heads/wf/wf-test:asset.txt')).toBe('saved asset');
    expect(raw.prepare("SELECT deploy_number, is_active FROM workflow_deploy_history WHERE workflow_id = 'wf-test'").all())
      .toEqual([{ deploy_number: 1, is_active: 1 }]);
  });

  it('rejects a saved candidate missing deployed files without advancing refs, tags, or history', async () => {
    writeFileSync(path.join(work, 'scripts/only-deployed.sh'), 'important script\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'deployed script');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const previous = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    git(work, 'tag', '-a', 'deploy/wf-test/#1', '-m', 'previous deployment');
    git(work, 'push', 'origin', 'refs/tags/deploy/wf-test/#1');
    raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('pack-1', 'wf-test', 1, 1, 'deploy/wf-test/#1', 'deploy', '{}', 1)`).run();
    const history = raw.prepare('SELECT * FROM workflow_deploy_history').all();
    const tag = git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1');
    await expect(editorDeploy()).rejects.toMatchObject({ status: 409, code: 'DEPLOY_TREE_CONFLICT',
      paths: ['scripts/only-deployed.sh'] });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(previous);
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1')).toBe(tag);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('deploy/wf-test/#1');
    expect(raw.prepare('SELECT * FROM workflow_deploy_history').all()).toEqual(history);
  });

  it('rejects all missing paths across the whole replaced tree and reports unusual names', async () => {
    mkdirSync(path.join(work, 'other-pack'));
    writeFileSync(path.join(work, 'other-pack/image.bin'), Buffer.from([0, 255, 1]));
    writeFileSync(path.join(work, 'scripts/line\nbreak.sh'), 'script');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'extra assets');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const previous = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    await expect(editorDeploy()).rejects.toMatchObject({ status: 409, code: 'DEPLOY_TREE_CONFLICT',
      paths: ['other-pack', 'other-pack/image.bin', 'scripts/line\nbreak.sh'] });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(previous);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('rejects a candidate changing the executable mode of an existing path', async () => {
    git(work, 'update-index', '--chmod=+x', 'scripts/run.sh');
    git(work, 'commit', '-m', 'executable deployment');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const previous = git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test');
    await expect(editorDeploy()).rejects.toMatchObject({ status: 409, code: 'DEPLOY_TREE_CONFLICT', paths: ['scripts/run.sh'] });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(previous);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('allows unrelated histories and changed file contents when all paths and modes survive', async () => {
    const unrelated = git(work, 'commit-tree', `${candidate}^{tree}`, '-m', 'unrelated old deployment');
    git(work, 'push', 'origin', `${unrelated}:refs/heads/wf/wf-test`);
    writeFileSync(path.join(work, 'asset.txt'), 'changed asset');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'saved update');
    candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    const result = await editorDeploy();
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(result.commit);
    expect(git(root, '--git-dir', remote, 'show', `${result.commit}:asset.txt`)).toBe('changed asset');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  });

  it('rejects changed deployed blobs outside a nested Pack for editor only', async () => {
    mkdirSync(path.join(work, 'packs')); mkdirSync(path.join(work, 'packs/pack-1'));
    renameSync(path.join(work, 'workflow.pack.yaml'), path.join(work, 'packs/pack-1/workflow.pack.yaml'));
    renameSync(path.join(work, 'workflows'), path.join(work, 'packs/pack-1/workflows'));
    mkdirSync(path.join(work, 'packs/pack-1-extra'));
    writeFileSync(path.join(work, 'packs/pack-1-extra/file'), 'deployed');
    git(work, 'add', '-A'); git(work, 'commit', '-m', 'nested active');
    const previous = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    writeFileSync(path.join(work, 'packs/pack-1-extra/file'), 'changed');
    git(work, 'add', '-A'); git(work, 'commit', '-m', 'nested candidate');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    await expect(editorDeploy()).rejects.toMatchObject({ status: 409, code: 'DEPLOY_TREE_CONFLICT', paths: ['packs/pack-1-extra/file'] });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(previous);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    const result = await deploy();
    expect(git(root, '--git-dir', remote, 'show', `${result.commit}:packs/pack-1-extra/file`)).toBe('changed');
  });

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

  it('rejects execute-only exact snapshot deployment but preserves legacy AutoHeal execute permission', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const executeOnly = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => false } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, async () => true);
    const clone = vi.spyOn(gitExecution, 'withGitOperation');
    await expect(executeOnly.editorDeploy({ workflowId: 'wf-test', actorId: 'viewer', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' }))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    expect(clone).not.toHaveBeenCalled();
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(await executeOnly.deploy({ workflowId: 'wf-test', actorId: 'viewer', candidateCommit: candidate, autoRun: false }))
      .toMatchObject({ ok: true, deployed: true });
  });

  it('requires edit and full candidate ACL for non-v2 Editor deployment', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const make = (edit: boolean, acl?: (candidate: { actorId: string; workflowId: string; packId: string; candidateCommit: string }) => Promise<boolean>) =>
      new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
        { hasExecutePermission: async () => true, hasEditPermission: async () => edit } as never,
        { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, acl);
    const input = { workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, expectedDraftToken: token(), autoRun: false };
    const allowed = vi.fn(async () => true);
    await expect(make(false, allowed).editorDeploy(input)).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(allowed).not.toHaveBeenCalled();
    await expect(make(true).editorDeploy(input)).rejects.toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    await expect(make(true, async () => false).editorDeploy(input)).rejects.toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    const policy = vi.fn(async (_value: { actorId: string; workflowId: string; packId: string; candidateCommit: string }) => true);
    const result = await make(true, policy).editorDeploy(input);
    expect(policy).toHaveBeenCalledTimes(2);
    const approved = policy.mock.calls[0][0];
    expect(approved).toMatchObject({ actorId: 'actor', workflowId: 'wf-test', packId: 'pack-1' });
    expect(approved.candidateCommit).not.toBe(candidate);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^`)).toBe(approved.candidateCommit);
    expect(policy.mock.calls[1][0]).toEqual(approved);
  });

  it('uses workflow edit for snapshot v2 when no additional host policy is configured', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => false, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    await expect(guarded.editorDeploymentState('wf-test', 'actor')).resolves.toMatchObject({ savedCommit: candidate, activeCommit: null });
    const result = await guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', packId: 'pack-1', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2', autoRun: false });
    expect(result.candidateCommit).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
  });

  it('applies the configured v2 policy to state and twice to the exact published candidate', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const authorize = vi.fn(async (_candidate: { actorId: string; workflowId: string; packId: string; candidateCommit: string }) => true);
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => false, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, authorize);
    await expect(guarded.editorDeploymentState('wf-test', 'actor')).resolves.toMatchObject({ savedCommit: candidate });
    const result = await guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2', autoRun: false });
    expect(result.candidateCommit).toBe(candidate);
    expect(authorize).toHaveBeenCalledTimes(3);
    for (const [checked] of authorize.mock.calls) expect(checked).toEqual({ actorId: 'actor', workflowId: 'wf-test', packId: 'pack-1', candidateCommit: candidate });
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
  });

  it.each(['denied', 'error', 'non-boolean'] as const)('fails closed for configured v2 policy: %s', async mode => {
    const history = new WorkflowDeployHistoryRepository(db);
    const authorize = vi.fn(async () => {
      if (mode === 'error') throw new Error('private ACL failure');
      return mode === 'non-boolean' ? null as unknown as boolean : false;
    });
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, authorize);
    const before = git(root, '--git-dir', remote, 'show-ref', '--heads', '--tags');
    const code = mode === 'error' ? 'SNAPSHOT_DEPLOY_AUTHORIZATION_UNAVAILABLE' : 'SNAPSHOT_DEPLOY_FORBIDDEN';
    await expect(guarded.editorDeploymentState('wf-test', 'actor')).rejects.toMatchObject({ code });
    await expect(guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' })).rejects.toMatchObject({ code });
    expect(authorize).toHaveBeenCalledTimes(2);
    expect(git(root, '--git-dir', remote, 'show-ref', '--heads', '--tags')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it.each(['denied', 'error'] as const)('rejects v2 policy %s on recheck before publication', async mode => {
    const history = new WorkflowDeployHistoryRepository(db);
    const authorize = vi.fn().mockResolvedValueOnce(true).mockImplementationOnce(async () => {
      if (mode === 'error') throw new Error('private ACL failure');
      return false;
    });
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, authorize);
    const push = vi.spyOn(deployGit, 'pushPreparedDeployment');
    const before = git(root, '--git-dir', remote, 'show-ref', '--heads', '--tags');
    const code = mode === 'error' ? 'SNAPSHOT_DEPLOY_AUTHORIZATION_UNAVAILABLE' : 'SNAPSHOT_DEPLOY_FORBIDDEN';
    await expect(guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' })).rejects.toMatchObject({ code });
    expect(authorize).toHaveBeenCalledTimes(2);
    expect(authorize).toHaveBeenNthCalledWith(1, { actorId: 'actor', workflowId: 'wf-test', packId: 'pack-1', candidateCommit: candidate });
    expect(authorize).toHaveBeenNthCalledWith(2, { actorId: 'actor', workflowId: 'wf-test', packId: 'pack-1', candidateCommit: candidate });
    expect(push).not.toHaveBeenCalled();
    expect(git(root, '--git-dir', remote, 'show-ref', '--heads', '--tags')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it.each(['missing', 'denied', 'error'] as const)('still fails closed when non-v2 Editor policy is %s', async mode => {
    const history = new WorkflowDeployHistoryRepository(db);
    const authorize = mode === 'missing' ? undefined : vi.fn(async () => {
      if (mode === 'error') throw new Error('private ACL failure');
      return false;
    });
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, authorize);
    const code = mode === 'error' ? 'SNAPSHOT_DEPLOY_AUTHORIZATION_UNAVAILABLE' : 'SNAPSHOT_DEPLOY_FORBIDDEN';
    await expect(guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), autoRun: false })).rejects.toMatchObject({ code });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
  });

  it('rechecks the non-v2 candidate policy before push and rejects revocation', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const authorize = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, authorize);
    const push = vi.spyOn(deployGit, 'pushPreparedDeployment');
    await expect(guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', packId: 'pack-1', candidateCommit: candidate,
      expectedDraftToken: token(), autoRun: false })).rejects.toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    expect(authorize).toHaveBeenCalledTimes(2); expect(push).not.toHaveBeenCalled();
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('rechecks workflow edit permission before pushing an exact snapshot', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const edit = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    const guarded = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: edit } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, async () => true);
    const push = vi.spyOn(deployGit, 'pushPreparedDeployment');
    await expect(guarded.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' }))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    expect(edit).toHaveBeenCalledTimes(2); expect(push).not.toHaveBeenCalled();
  });

  it('reports Git success and unknown DB outcome if active release races after push', async () => {
    const push = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await push(...args);
      raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
        VALUES ('pack-1', 'wf-test', 7, 7, 'concurrent', 'deploy', '{}', 1)`).run();
      return result;
    });
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' }))
      .rejects.toMatchObject({ code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).not.toBe(candidate);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toContain('deploy/wf-test/#1');
    expect(raw.prepare('SELECT deploy_number, is_active FROM workflow_deploy_history').all()).toEqual([{ deploy_number: 7, is_active: 1 }]);
  });

  it('proves absence and promotes the first exact snapshot with an empty lease', async () => {
    expect(await service.editorDeploymentState('wf-test', 'actor')).toEqual({ protocol: 'editor-deployment-state-v1', workflowId: 'wf-test', packId: 'pack-1', canDeploy: true, savedCommit: candidate, activeCommit: null });
    const result = await service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2', autoRun: false });
    expect(result.expectedActiveCommit).toBe('');
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history WHERE is_active = 1').get()).toEqual({ n: 1 });
  });

  it('rejects absence proof when DB has an active release or actor lacks permission', async () => {
    raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('pack-1', 'wf-test', 1, 1, 'tag', 'deploy', '{}', 1)`).run();
    await expect(service.editorDeploymentState('wf-test', 'actor')).rejects.toMatchObject({ code: 'DEPLOY_BASELINE_MISSING' });
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' })).rejects.toMatchObject({ code: 'DEPLOY_BASELINE_MISSING' });
    const history = new WorkflowDeployHistoryRepository(db);
    const denied = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => false, hasEditPermission: async () => false } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    await expect(denied.editorDeploymentState('wf-test', 'actor')).rejects.toMatchObject({ code: 'FORBIDDEN' });
    const executeOnly = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => true, hasEditPermission: async () => false } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    await expect(executeOnly.editorDeploymentState('wf-test', 'actor')).rejects.toMatchObject({ code: 'FORBIDDEN' });
  });

  it('rejects an active DB release appearing during first deployment preparation', async () => {
    const prepare = deployGit.prepareDeployCandidate;
    vi.spyOn(deployGit, 'prepareDeployCandidate').mockImplementationOnce(async (...args) => {
      const result = await prepare(...args);
      raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
        VALUES ('pack-1', 'wf-test', 7, 7, 'other', 'deploy', '{}', 1)`).run();
      return result;
    });
    const push = vi.spyOn(deployGit, 'pushPreparedDeployment');
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' })).rejects.toMatchObject({ code: 'DEPLOY_BASELINE_MISSING' });
    expect(push).not.toHaveBeenCalled();
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
  });

  it('rejects unseen files outside a nested Pack on first deploy', async () => {
    mkdirSync(path.join(work, 'packs')); mkdirSync(path.join(work, 'packs/pack-1'));
    for (const name of ['workflow.pack.yaml', 'workflows', 'scripts']) renameSync(path.join(work, name), path.join(work, 'packs/pack-1', name));
    git(work, 'add', '-A'); git(work, 'commit', '-m', 'nested saved'); candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' })).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT' });
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
  });

  it('does not replace a concurrently created wf on first deploy', async () => {
    const push = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      git(work, 'push', 'origin', `${candidate}:refs/heads/wf/wf-test`);
      return push(...args);
    });
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: '', confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2' })).rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
  });

  it('publishes the confirmed saved tree and exact YAML bytes with snapshot v2', async () => {
    const previous = candidate; git(work, 'push', 'origin', `${previous}:refs/heads/wf/wf-test`);
    const yaml = '# Preserve formatting and comments\n' + stringify(spec);
    writeFileSync(path.join(work, 'workflows/wf-test.yaml'), yaml);
    git(work, 'add', '.'); git(work, 'commit', '-m', 'snapshot');
    candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    const result = await service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: previous, confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2', autoRun: false });
    expect(result.snapshotProtocol).toBe('saved-tree-v2');
    expect(result.candidateCommit).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(git(root, '--git-dir', remote, 'rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(result.commit);
    expect(git(root, '--git-dir', remote, 'show', `${result.commit}:workflows/wf-test.yaml`)).toBe(yaml.trim());
    expect(git(root, '--git-dir', remote, 'cat-file', '-p', `refs/tags/${result.tagName}`)).toContain(`candidateCommit=${candidate}`);
    const history = raw.prepare('SELECT spec_json, is_active FROM workflow_deploy_history').get() as { spec_json: string; is_active: number };
    expect(history.is_active).toBe(1); expect(JSON.parse(history.spec_json).content).toBe(yaml);
  });

  it('rejects divergent DB, wrong candidate, missing saved ref and stale draft before publication', async () => {
    git(work, 'push', 'origin', `${candidate}:refs/heads/wf/wf-test`);
    const expectedDraftToken = token();
    const input = { workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, expectedDraftToken,
      snapshotProtocol: 'saved-tree-v2' as const, expectedActiveCommit: candidate, confirmedFileDiff: true as const, autoRun: false };
    await expect(service.editorDeploy({ ...input, candidateCommit: 'a'.repeat(40) })).rejects.toMatchObject({ code: 'CANDIDATE_NOT_SAVED' });
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'different' }));
    await expect(service.editorDeploy(input)).rejects.toMatchObject({ code: 'DRAFT_CONFLICT' });
    await expect(service.editorDeploy({ ...input, expectedDraftToken: token() })).rejects.toMatchObject({ code: 'SNAPSHOT_DB_MISMATCH' });
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify(spec));
    git(work, 'push', 'origin', ':refs/heads/saved/wf-test');
    git(work, 'push', 'origin', `${candidate}:refs/heads/wf/wf-test`);
    await expect(service.editorDeploy({ ...input, expectedDraftToken: token() })).rejects.toMatchObject({ code: 'SAVE_REQUIRED' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('rejects snapshot when saved advances during preparation', async () => {
    git(work, 'push', 'origin', `${candidate}:refs/heads/wf/wf-test`);
    const prepare = deployGit.prepareDeployCandidate;
    vi.spyOn(deployGit, 'prepareDeployCandidate').mockImplementationOnce(async (...args) => {
      const result = await prepare(...args);
      writeFileSync(path.join(work, 'asset.txt'), 'concurrent save');
      git(work, 'add', '.'); git(work, 'commit', '-m', 'concurrent save');
      git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
      return result;
    });
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: candidate, confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2', autoRun: false }))
      .rejects.toMatchObject({ code: 'CANDIDATE_NOT_SAVED' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(candidate);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('does not activate a snapshot if the database changes after Git publication', async () => {
    git(work, 'push', 'origin', `${candidate}:refs/heads/wf/wf-test`);
    const push = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await push(...args);
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'concurrent' }));
      return result;
    });
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: candidate, confirmedFileDiff: true, snapshotProtocol: 'saved-tree-v2', autoRun: false }))
      .rejects.toMatchObject({ code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/tags/deploy/wf-test/#1^{tree}')).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('enforces existing permissions and tree preservation for snapshot v2', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const forbidden = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasExecutePermission: async () => false, hasEditPermission: async () => false } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    const input = { workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), expectedActiveCommit: candidate, confirmedFileDiff: true as const, snapshotProtocol: 'saved-tree-v2' as const, autoRun: false };
    await expect(forbidden.editorDeploy(input)).rejects.toMatchObject({ code: 'FORBIDDEN' });
    writeFileSync(path.join(work, 'only-active.txt'), 'do not lose');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'active extra');
    const previous = git(work, 'rev-parse', 'HEAD');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    await expect(service.editorDeploy({ ...input, expectedActiveCommit: previous })).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT', paths: ['only-active.txt'] });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(previous);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it.each(['root', 'nested'] as const)('requires the reviewed active commit and publishes explicitly confirmed %s Pack script changes', async layout => {
    if (layout === 'nested') {
      mkdirSync(path.join(work, 'packs')); mkdirSync(path.join(work, 'packs/pack-1'));
      for (const name of ['workflow.pack.yaml', 'workflows', 'scripts']) renameSync(path.join(work, name), path.join(work, 'packs/pack-1', name));
      git(work, 'add', '-A'); git(work, 'commit', '-m', 'nested active');
    }
    const active = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', `${active}:refs/heads/wf/wf-test`);
    const script = layout === 'root' ? 'scripts/run.sh' : 'packs/pack-1/scripts/run.sh';
    writeFileSync(path.join(work, script), 'saved replacement script\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'saved changed script');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    const input = { workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), snapshotProtocol: 'saved-tree-v2' as const, autoRun: false };
    await expect(service.editorDeploy(input)).rejects.toMatchObject({ code: 'INVALID_REQUEST' });
    await expect(service.editorDeploy({ ...input, expectedActiveCommit: active })).rejects.toMatchObject({ code: 'INVALID_REQUEST' });
    await expect(service.editorDeploy({ ...input, expectedActiveCommit: 'a'.repeat(40), confirmedFileDiff: true })).rejects.toMatchObject({ code: 'DEPLOY_BASELINE_STALE' });
    const result = await service.editorDeploy({ ...input, expectedActiveCommit: active, confirmedFileDiff: true });
    expect(result.expectedActiveCommit).toBe(active);
    expect(git(root, '--git-dir', remote, 'rev-parse', `${result.commit}^{tree}`)).toBe(git(work, 'rev-parse', `${candidate}^{tree}`));
    expect(git(root, '--git-dir', remote, 'show', `${result.commit}:${script}`)).toBe('saved replacement script');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history WHERE is_active = 1').get()).toEqual({ n: 1 });
  });

  it('rejects nested Pack changes outside the inventory, including new outside files', async () => {
    mkdirSync(path.join(work, 'packs')); mkdirSync(path.join(work, 'packs/pack-1'));
    for (const name of ['workflow.pack.yaml', 'workflows', 'scripts']) renameSync(path.join(work, name), path.join(work, 'packs/pack-1', name));
    git(work, 'add', '-A'); git(work, 'commit', '-m', 'nested baseline');
    const active = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', `${active}:refs/heads/wf/wf-test`);
    writeFileSync(path.join(work, 'outside-new.txt'), 'not shown in Pack preview');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'outside addition');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/saved/wf-test');
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedActiveCommit: active, confirmedFileDiff: true, expectedDraftToken: token(), snapshotProtocol: 'saved-tree-v2' }))
      .rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(active);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
  });

  it('does not overwrite another client advancing wf after the final ref check', async () => {
    git(work, 'push', 'origin', `${candidate}:refs/heads/wf/wf-test`);
    writeFileSync(path.join(work, 'scripts/run.sh'), 'newer active script');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'other client');
    const concurrent = git(work, 'rev-parse', 'HEAD');
    const push = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      git(work, 'push', 'origin', `${concurrent}:refs/heads/wf/wf-test`);
      return push(...args);
    });
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedActiveCommit: candidate, confirmedFileDiff: true, expectedDraftToken: token(), snapshotProtocol: 'saved-tree-v2' }))
      .rejects.toMatchObject({ code: 'DEPLOY_OUTCOME_UNKNOWN' });
    expect(git(root, '--git-dir', remote, 'rev-parse', 'refs/heads/wf/wf-test')).toBe(concurrent);
    expect(git(root, '--git-dir', remote, 'tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

});
