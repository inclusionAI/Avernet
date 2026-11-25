// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import { editorDraftToken } from '../editor-draft-token.js';
import { cloneClawflows } from '../clawflows-git-client.js';
import { runGit } from './clawflows-git-client-fixture.js';
import * as deployGit from '../auto-heal-deploy-git.js';
import { inspectDeployCandidate, prepareDeployCandidate, pushPreparedDeployment } from '../auto-heal-deploy-git.js';

vi.setConfig({ testTimeout: 45_000, hookTimeout: 30_000 });

const spec = { id: 'wf-test', title: 'Candidate', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
const author = { name: 'Tester', email: 'tester@example.invalid' };
const git = (dir: string, ...args: string[]) => runGit(dir, args);

function manifest(packId = 'pack-1', workflowId = 'wf-test', workflowFile = 'workflows/wf-test.yaml') {
  return stringify({ id: packId, version: '1.0.0', workflows: [{ id: workflowId, file: workflowFile }] });
}

describe('exact wf candidate deployment', () => {
  let root: string, remote: string, work: string, candidate: string;
  let raw: Database.Database, db: SqliteDatabase, service: AutoHealDeployService;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'candidate-deploy-'));
    remote = path.join(root, 'remote.git'); work = path.join(root, 'work');
    git(root, 'init', '--bare', '--initial-branch=main', remote);
    git(root, 'init', '--initial-branch=main', work);
    git(work, 'config', 'user.name', author.name); git(work, 'config', 'user.email', author.email);
    mkdirSync(path.join(work, 'workflows')); mkdirSync(path.join(work, 'scripts'));
    writeFileSync(path.join(work, 'workflow.pack.yaml'), manifest());
    writeFileSync(path.join(work, 'workflows/wf-test.yaml'), stringify(spec));
    writeFileSync(path.join(work, 'scripts/run.sh'), 'saved script\n');
    writeFileSync(path.join(work, 'asset.txt'), 'saved asset\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'saved Pack'); candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'remote', 'add', 'origin', remote);
    git(work, 'push', 'origin', `HEAD:refs/heads/main`, `HEAD:refs/heads/wf/wf-test`);
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

  afterEach(async () => {
    vi.restoreAllMocks();
    await db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });

  const token = () => editorDraftToken(raw.prepare('SELECT * FROM workflow_specs WHERE workflow_id = ?').get('wf-test') as never);
  const autoDeploy = (extra: Record<string, unknown> = {}) => service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false, ...extra });
  const editorDeploy = (active = '', extra: Record<string, unknown> = {}) => service.editorDeploy({
    workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false, expectedDraftToken: token(),
    snapshotProtocol: 'saved-tree-v2', expectedActiveCommit: active, confirmedFileDiff: true, ...extra,
  });
  const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);

  function activateProduction(commit: string, deployNumber = 1, version = deployNumber, tag = `deploy/wf-test/#${deployNumber}`) {
    git(work, 'tag', '-a', tag, commit, '-m', 'production');
    git(work, 'push', 'origin', `refs/tags/${tag}`);
    raw.prepare(`INSERT INTO workflow_deploy_history
      (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES (?, ?, ?, ?, ?, 'deploy', ?, 1)`).run('pack-1', 'wf-test', deployNumber, version, tag, JSON.stringify(spec));
  }

  it.each(['autoheal', 'editor'])('publishes the exact wf candidate on first deployment (%s)', async route => {
    const beforeBranches = remoteGit('show-ref', '--heads');
    const result = route === 'autoheal' ? await autoDeploy() : await editorDeploy();
    expect(result).toMatchObject({ deployed: true, candidateCommit: candidate, commit: candidate, deployNumber: 1 });
    expect(remoteGit('show-ref', '--heads')).toBe(beforeBranches);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(candidate);
    expect(raw.prepare('SELECT deploy_number, is_active FROM workflow_deploy_history WHERE workflow_id = ?').all('wf-test'))
      .toEqual([{ deploy_number: 1, is_active: 1 }]);
  });

  it('treats an existing wf ref as the first deployment when no active history exists', async () => {
    expect(raw.prepare('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1').all('wf-test')).toEqual([]);
    const result = await autoDeploy();
    expect(result.deployNumber).toBe(1);
    expect(remoteGit('show-ref', '--heads')).toContain(`${candidate} refs/heads/wf/wf-test`);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(candidate);
  });

  it.each(['autoheal', 'editor'])('rejects an unsaved DB-only edit instead of overlaying it (%s)', async route => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'DB only draft' }));
    const beforeRefs = remoteGit('show-ref');
    const call = route === 'autoheal' ? autoDeploy() : editorDeploy();
    await expect(call).rejects.toMatchObject({ status: 409, code: 'SNAPSHOT_DB_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(beforeRefs);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('fails closed for an unparseable DB draft on v2 without touching the tag or active history', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run('{not valid json');
    const beforeRefs = remoteGit('show-ref');
    const beforeDraft = raw.prepare('SELECT * FROM workflow_specs').get();
    await expect(editorDeploy()).rejects.toMatchObject({ status: 400, code: 'INVALID_WORKFLOW' });
    expect(remoteGit('show-ref')).toBe(beforeRefs);
    expect(raw.prepare('SELECT * FROM workflow_specs').get()).toEqual(beforeDraft);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('uses the active history tag as production while wf contains a newer draft', async () => {
    const production = candidate;
    activateProduction(production);
    writeFileSync(path.join(work, 'asset.txt'), 'newer saved asset\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'newer wf draft');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const state = await service.editorDeploymentState('wf-test', 'actor');
    expect(state).toMatchObject({ savedCommit: candidate, activeCommit: production });
    const result = await editorDeploy(production);
    expect(result.commit).toBe(candidate);
    expect(result.candidateCommit).toBe(candidate);
    expect(remoteGit('show-ref', '--heads')).toContain(`${candidate} refs/heads/wf/wf-test`);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(candidate);
  });

  it('fails closed when active history names a missing or invalid tag', async () => {
    raw.prepare(`INSERT INTO workflow_deploy_history
      (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, is_active)
      VALUES ('pack-1', 'wf-test', 1, 1, 'not-a-deployment-tag', 'deploy', ?, 1)`).run(JSON.stringify(spec));
    const before = remoteGit('show-ref');
    await expect(autoDeploy()).rejects.toMatchObject({ status: 409, code: 'DEPLOY_BASELINE_MISSING' });
    expect(remoteGit('show-ref')).toBe(before);
  });

  it('prepares and pushes a tag for the frozen candidate without touching branches', async () => {
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const inspected = await inspectDeployCandidate(clone, 'wf-test', 'pack-1', candidate);
    const prepared = await prepareDeployCandidate(clone, 'wf-test', candidate, inspected.previousCommit, 1, 1, author);
    expect(prepared.previousCommit).toBe('');
    expect(prepared.releaseCommit).toBe(candidate);
    expect(prepared.commit).toBe(candidate);
    const beforeBranches = remoteGit('show-ref', '--heads');
    await pushPreparedDeployment(clone, prepared);
    expect(remoteGit('show-ref', '--heads')).toBe(beforeBranches);
    expect(remoteGit('rev-parse', `refs/tags/${prepared.tagName}^{commit}`)).toBe(candidate);
  });

  it('publishes a frozen candidate even when wf advances after preparation', async () => {
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const prepared = await prepareDeployCandidate(clone, 'wf-test', candidate, '', 1, 1, author);
    writeFileSync(path.join(work, 'asset.txt'), 'advanced wf draft\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'advance wf');
    const newer = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', `HEAD:refs/heads/wf/wf-test`);
    await pushPreparedDeployment(clone, prepared);
    expect(remoteGit('rev-parse', 'refs/heads/wf/wf-test')).toBe(newer);
    expect(remoteGit('rev-parse', `refs/tags/${prepared.tagName}^{commit}`)).toBe(candidate);
  });

  it('does not create a new number when the exact deployment tag already exists', async () => {
    git(work, 'tag', '-a', 'deploy/wf-test/#1', candidate, '-m', 'existing');
    git(work, 'push', 'origin', 'refs/tags/deploy/wf-test/#1');
    const clone = path.join(root, 'clone'); mkdirSync(clone);
    await cloneClawflows(clone, { remoteUrl: remote, username: author.name, email: author.email });
    const prepared = await prepareDeployCandidate(clone, 'wf-test', candidate, '', 1, 1, author);
    await expect(pushPreparedDeployment(clone, prepared)).rejects.toMatchObject({ name: 'DeploymentOutcomeUnknownError' });
    expect(remoteGit('rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(candidate);
    expect(remoteGit('tag', '-l')).toBe('deploy/wf-test/#1');
  });

  it('reports an uncertain tag push without changing wf or activating history', async () => {
    const beforeBranches = remoteGit('show-ref', '--heads');
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await realPush(...args);
      throw Object.assign(new Error('lost acknowledgement'), { result });
    });
    await expect(autoDeploy({ requestId: 'tag-unknown-01' })).rejects.toMatchObject({
      status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN', requestId: 'tag-unknown-01', tagName: 'deploy/wf-test/#1',
    });
    expect(remoteGit('show-ref', '--heads')).toBe(beforeBranches);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(candidate);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('preserves active scripts, resources, and modes by comparing against the active tag', async () => {
    writeFileSync(path.join(work, 'scripts/production-only.sh'), 'must survive\n', { mode: 0o755 });
    git(work, 'add', '.'); git(work, 'commit', '-m', 'production resource');
    const production = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    activateProduction(production);
    rmSync(path.join(work, 'scripts/production-only.sh'));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'draft removes production resource');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const before = remoteGit('show-ref');
    await expect(editorDeploy(production)).rejects.toMatchObject({ status: 409, code: 'DEPLOY_TREE_CONFLICT', paths: ['scripts/production-only.sh'] });
    expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT deploy_number, is_active FROM workflow_deploy_history').all()).toEqual([{ deploy_number: 1, is_active: 1 }]);
  });

  it('rejects a foreign facade owner before creating a tag', async () => {
    writeFileSync(path.join(work, 'workflow.pack.yaml'), manifest() + 'facades:\n  - command: main-command\n    aliases: [alias-command]\n    defaultWorkflow: wf-test\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'facade candidate');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    raw.prepare('INSERT INTO facade_bindings (command, workflow_id, pack_id) VALUES (?, ?, ?)').run('main-command', 'other', 'other-pack');
    const before = remoteGit('show-ref');
    await expect(autoDeploy()).rejects.toMatchObject({ status: 409, code: 'FACADE_CONFLICT' });
    expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT command, workflow_id, pack_id FROM facade_bindings').all()).toEqual([{ command: 'main-command', workflow_id: 'other', pack_id: 'other-pack' }]);
  });

  it('updates same-workflow facades while publishing the exact candidate', async () => {
    writeFileSync(path.join(work, 'workflow.pack.yaml'), manifest() + 'facades:\n  - command: main-command\n    aliases: [alias-command]\n    defaultWorkflow: wf-test\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'facade candidate');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    raw.prepare('INSERT INTO facade_bindings (command, workflow_id, pack_id) VALUES (?, ?, ?)').run('main-command', 'wf-test', 'old-pack');
    await autoDeploy();
    expect(raw.prepare('SELECT command, workflow_id, pack_id FROM facade_bindings ORDER BY command').all()).toEqual([
      { command: 'alias-command', workflow_id: 'wf-test', pack_id: 'pack-1' },
      { command: 'main-command', workflow_id: 'wf-test', pack_id: 'pack-1' },
    ]);
  });

  it('keeps the legacy AutoHeal execute permission but requires edit for exact Editor deployment', async () => {
    const executeOnly = new AutoHealDeployService(db, new WorkflowSpecRepository(db, new WorkflowDeployHistoryRepository(db)), new WorkflowDeployHistoryRepository(db),
      { hasExecutePermission: async () => true, hasEditPermission: async () => false } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    await expect(executeOnly.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate,
      expectedDraftToken: token(), snapshotProtocol: 'saved-tree-v2', expectedActiveCommit: '', confirmedFileDiff: true }))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    await expect(executeOnly.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, autoRun: false }))
      .resolves.toMatchObject({ deployed: true, commit: candidate });
  });

  it('rejects a stale Editor draft token before any publication', async () => {
    const stale = token();
    raw.prepare('UPDATE workflow_specs SET spec_json = ?, gmt_modified = gmt_modified + 1').run(JSON.stringify({ ...spec, title: 'new draft' }));
    const before = remoteGit('show-ref');
    await expect(service.editorDeploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: candidate, expectedDraftToken: stale,
      snapshotProtocol: 'saved-tree-v2', expectedActiveCommit: '', confirmedFileDiff: true })).rejects.toMatchObject({ status: 409, code: 'DRAFT_CONFLICT' });
    expect(remoteGit('show-ref')).toBe(before);
  });

  it('rejects a stale candidate that no longer equals wf', async () => {
    const stale = candidate;
    writeFileSync(path.join(work, 'asset.txt'), 'newer candidate\n');
    git(work, 'add', '.'); git(work, 'commit', '-m', 'new candidate');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    const before = remoteGit('show-ref');
    await expect(service.deploy({ workflowId: 'wf-test', actorId: 'actor', candidateCommit: stale, autoRun: false }))
      .rejects.toMatchObject({ status: 409, code: 'CANDIDATE_NOT_SAVED' });
    expect(remoteGit('show-ref')).toBe(before);
  });

  it('rejects lossy or unsafe DB JSON before changing Git or history', async () => {
    for (const text of [
      '{"id":"wf-test","title":"Candidate","nodes":[],"custom":0.10000000000000001}',
      '{"id":"wf-test","title":"Candidate","nodes":[],"custom":9007199254740993}',
      '{"id":"wf-test","title":"Candidate","nodes":[],"custom":{"key":1,"key":2}}',
      '{"id":"wf-test","title":"Candidate","nodes":[],"custom":-0}',
    ]) {
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
      const before = remoteGit('show-ref');
      await expect(autoDeploy()).rejects.toMatchObject({ status: 400, code: 'INVALID_WORKFLOW' });
      expect(remoteGit('show-ref')).toBe(before);
      expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    }
  });

  it('does not activate a release when the DB changes after the tag push', async () => {
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await realPush(...args);
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'concurrent draft' }));
      return result;
    });
    await expect(autoDeploy()).rejects.toMatchObject({ status: 503, code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(remoteGit('show-ref', '--heads')).toContain(`${candidate} refs/heads/wf/wf-test`);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/wf-test/#1^{commit}')).toBe(candidate);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('rejects a candidate with an invalid workflow identity without publication', async () => {
    writeFileSync(path.join(work, 'workflows/wf-test.yaml'), stringify({ ...spec, id: 'other' }));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'invalid identity');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/wf-test');
    await expect(autoDeploy()).rejects.toThrow('Workflow identity mismatch');
    expect(remoteGit('tag', '-l')).toBe('');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
});
