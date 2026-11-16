// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { stringify } from 'yaml';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import { editorDraftToken } from '../editor-draft-token.js';
import * as deployment from '../auto-heal-deploy-git.js';
import { runGit } from './clawflows-git-client-fixture.js';

const author = { name: 'Fixture', email: 'fixture@example.invalid' };
const spec = { id: 'flow', title: 'Saved document', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
let root: string, remote: string, repo: string, candidate: string;
let raw: Database.Database, db: SqliteDatabase, service: AutoHealDeployService;
const git = (...args: string[]) => runGit(repo, args);
const remoteGit = (...args: string[]) => runGit(root, ['--git-dir', remote, ...args]);
function write(file: string, text: string) { mkdirSync(path.dirname(path.join(repo, file)), { recursive: true }); writeFileSync(path.join(repo, file), text); }
function commit() { git('add', '-A'); git('commit', '-m', 'fixture'); return git('rev-parse', 'HEAD'); }
function save() { candidate = commit(); git('push', 'origin', `${candidate}:refs/heads/wf/flow`); return candidate; }
function draftToken() { return editorDraftToken(raw.prepare('SELECT * FROM workflow_specs').get() as never); }
const deploy = (route: string, active = '') => route === 'autoheal'
  ? service.deploy({ workflowId: 'flow', actorId: 'actor', candidateCommit: candidate, autoRun: false })
  : service.editorDeploy({ workflowId: 'flow', actorId: 'actor', candidateCommit: candidate, expectedDraftToken: draftToken(), autoRun: false,
    ...(route === 'v2' ? { snapshotProtocol: 'saved-tree-v2' as const, expectedActiveCommit: active, confirmedFileDiff: true as const } : {}) });

beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'tag-only-deploy-')); remote = path.join(root, 'remote.git'); repo = path.join(root, 'repo');
  runGit(root, ['init', '--bare', '--initial-branch=main', remote]); runGit(root, ['init', '--initial-branch=main', repo]);
  git('config', 'user.name', author.name); git('config', 'user.email', author.email); git('remote', 'add', 'origin', remote);
  write('workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n'); write('workflows/flow.yaml', '# exact saved bytes\n' + stringify(spec));
  write('scripts/run.sh', '#!/bin/sh\necho saved\n'); candidate = commit();
  git('push', 'origin', `${candidate}:refs/heads/main`, `${candidate}:refs/heads/wf/flow`);
  raw = new Database(':memory:');
  raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
  raw.prepare('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json) VALUES (?,?,?)').run('flow', 'pack', JSON.stringify(spec));
  db = new SqliteDatabase(raw); const history = new WorkflowDeployHistoryRepository(db);
  service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
    { hasEditPermission: async () => true, hasExecutePermission: async () => true } as never,
    { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, async () => true);
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

function activate(commit: string, tag = 'deploy/flow/#1') {
  git('tag', '-a', tag, commit, '-m', 'production'); git('push', 'origin', `refs/tags/${tag}`);
  raw.prepare(`INSERT INTO workflow_deploy_history (pack_id,workflow_id,deploy_number,version,tag_name,action,spec_json,is_active)
    VALUES ('pack','flow',1,1,?,'deploy',?,1)`).run(tag, JSON.stringify(spec));
}

describe('saved wf candidates publish only immutable tags and active history', () => {
  it('prepares and pushes a tag pointing to candidate itself without changing any branch', async () => {
    const before = remoteGit('show-ref', '--heads');
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, '', 1, 1, author);
    expect(prepared.releaseCommit).toBe(candidate); expect(prepared.commit).toBe(candidate);
    await deployment.pushPreparedDeployment(repo, prepared);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{commit}')).toBe(candidate);
    expect(remoteGit('show-ref', '--heads')).toBe(before);
  });

  it.each([false, true])('resumes only the immutable tag despite a newer wf draft (tag already exists: %s)', async exists => {
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, '', 1, 1, author);
    if (exists) await deployment.pushPreparedDeployment(repo, prepared);
    write('scripts/run.sh', 'newer wf draft\n'); save(); const branches = remoteGit('show-ref', '--heads');
    const result = await deployment.resumePreparedDeployment(prepared, { remoteUrl: remote, username: author.name, email: author.email });
    expect(result.commit).toBe(prepared.candidateCommit); expect(remoteGit('show-ref', '--heads')).toBe(branches);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{commit}')).toBe(prepared.candidateCommit);
  });

  it('rejects a prepared candidate identity mixed with an already published tag', async () => {
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, '', 1, 1, author);
    await deployment.pushPreparedDeployment(repo, prepared);
    write('scripts/run.sh', 'different candidate\n'); save(); const before = remoteGit('show-ref');
    const mixed = { ...prepared, candidateCommit: candidate, releaseCommit: candidate, commit: candidate };
    await expect(deployment.resumePreparedDeployment(mixed, { remoteUrl: remote, username: author.name, email: author.email })).rejects.toThrow();
    expect(remoteGit('show-ref')).toBe(before);
  });

  it.each(['version', 'metadata'])('rejects mixed prepared %s on an existing tag without completing history', async field => {
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, '', 1, 1, author);
    await deployment.pushPreparedDeployment(repo, prepared); const before = remoteGit('show-ref');
    const mixed = field === 'version' ? { ...prepared, version: 2 } : { ...prepared, content: 'id: flow\nnodes: []\n' };
    await expect(deployment.resumePreparedDeployment(mixed, { remoteUrl: remote, username: author.name, email: author.email })).rejects.toThrow();
    expect(remoteGit('show-ref')).toBe(before);
  });

  it('does not replace a concurrently occupied immutable deployment tag', async () => {
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, '', 1, 1, author);
    activate(candidate); const before = remoteGit('show-ref');
    await expect(deployment.pushPreparedDeployment(repo, prepared)).rejects.toThrow('outcome unknown');
    await expect(deployment.resumePreparedDeployment(prepared, { remoteUrl: remote, username: author.name, email: author.email })).rejects.toThrow();
    expect(remoteGit('show-ref')).toBe(before);
  });

  it.each(['editor', 'v2', 'autoheal'])('publishes exact saved wf for %s first deploy, without requiring legacy saved ref', async route => {
    const before = remoteGit('show-ref', '--heads'); const result = await deploy(route);
    expect(result.commit).toBe(candidate); expect(result.candidateCommit).toBe(candidate);
    expect(remoteGit('show-ref', '--heads')).toBe(before);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(candidate);
    const row = raw.prepare('SELECT * FROM workflow_deploy_history WHERE is_active = 1').get() as { spec_json: string; tag_name: string };
    expect(JSON.parse(row.spec_json).content).toBe('# exact saved bytes\n' + stringify(spec)); expect(row.tag_name).toBe(result.tagName);
  });

  it.each(['editor', 'v2', 'autoheal'])('rejects unsaved DB-only YAML for %s rather than overlaying during deploy', async route => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ ...spec, title: 'DB only unsaved' }));
    const before = remoteGit('show-ref'); await expect(deploy(route)).rejects.toMatchObject({ code: 'SNAPSHOT_DB_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(before); expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it.each(['editor', 'v2', 'autoheal'])('ignores and never fetches an unrelated saved ref for %s', async route => {
    const ignored = git('commit-tree', `${candidate}^{tree}`, '-m', 'ignored old saved ref');
    git('push', 'origin', `${ignored}:refs/heads/saved/flow`);
    const execute = vi.spyOn(gitExecution, 'executeGit');
    const prepare = deployment.prepareDeployCandidate; let inspected = false;
    vi.spyOn(deployment, 'prepareDeployCandidate').mockImplementationOnce(async (...args) => {
      inspected = true;
      expect(() => runGit(args[0], ['cat-file', '-e', `${ignored}^{commit}`])).toThrow();
      return prepare(...args);
    });
    const branches = remoteGit('show-ref', '--heads'); const result = await deploy(route);
    expect(result.commit).toBe(candidate); expect(inspected).toBe(true); expect(remoteGit('show-ref', '--heads')).toBe(branches);
    expect(execute.mock.calls.some(([args]) => args.includes('clone') || args.some(arg => arg.includes('saved/')))).toBe(false);
  });

  it.each(['editor', 'v2', 'autoheal'])('rejects only-saved with no wf saving baseline for %s without fetching it', async route => {
    remoteGit('update-ref', 'refs/heads/saved/flow', candidate); remoteGit('update-ref', '-d', 'refs/heads/wf/flow');
    const before = remoteGit('show-ref'); const execute = vi.spyOn(gitExecution, 'executeGit');
    await expect(deploy(route)).rejects.toMatchObject({ code: 'SAVE_REQUIRED' });
    expect(remoteGit('show-ref')).toBe(before); expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    expect(execute.mock.calls.some(([args]) => args.includes('clone') || args.some(arg => arg.includes('saved/')))).toBe(false);
  });

  it('reads Editor deployment state without querying or fetching saved refs', async () => {
    const ignored = git('commit-tree', `${candidate}^{tree}`, '-m', 'ignored state source');
    git('push', 'origin', `${ignored}:refs/heads/saved/flow`);
    const execute = vi.spyOn(gitExecution, 'executeGit');
    expect(await service.editorDeploymentState('flow', 'actor')).toMatchObject({ savedCommit: candidate, activeCommit: null });
    expect(execute.mock.calls.some(([args]) => args.includes('clone') || args.some(arg => arg.includes('saved/')))).toBe(false);
  });

  it.each([false, true])('resumes from only the tag and prepared objects, not saved refs (tag exists: %s)', async exists => {
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, '', 1, 1, author);
    if (exists) await deployment.pushPreparedDeployment(repo, prepared);
    const ignored = git('commit-tree', `${candidate}^{tree}`, '-m', 'ignored resume source');
    git('push', 'origin', `${ignored}:refs/heads/saved/flow`);
    const before = remoteGit('show-ref', '--heads'); const execute = vi.spyOn(gitExecution, 'executeGit');
    expect((await deployment.resumePreparedDeployment(prepared, { remoteUrl: remote, username: author.name, email: author.email })).commit).toBe(candidate);
    expect(remoteGit('show-ref', '--heads')).toBe(before);
    expect(execute.mock.calls.some(([args]) => args.includes('clone') || args.some(arg => arg.includes('refs/heads/')))).toBe(false);
  });

  it.each(['9', '09'])('allocates past remote deploy tag #%s without fetching branch refs', async number => {
    const tag = `deploy/flow/#${number}`;
    git('tag', '-a', tag, candidate, '-m', 'prior immutable tag'); git('push', 'origin', `refs/tags/${tag}`);
    const before = remoteGit('show-ref', '--heads'); const result = await deploy('autoheal');
    expect(result.deployNumber).toBe(10); expect(result.commit).toBe(candidate); expect(remoteGit('show-ref', '--heads')).toBe(before);
  });

  it('fetches only wf and resumes exact tags in a SHA-256 repository', async () => {
    remote = path.join(root, 'sha256-remote.git'); repo = path.join(root, 'sha256-repo');
    runGit(root, ['init', '--bare', '--initial-branch=main', '--object-format=sha256', remote]);
    runGit(root, ['init', '--initial-branch=main', '--object-format=sha256', repo]);
    git('config', 'user.name', author.name); git('config', 'user.email', author.email); git('remote', 'add', 'origin', remote);
    write('workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n'); write('workflows/flow.yaml', stringify(spec));
    write('scripts/run.sh', 'sha256 script\n'); candidate = commit();
    git('push', 'origin', `${candidate}:refs/heads/main`, `${candidate}:refs/heads/wf/flow`);
    const history = new WorkflowDeployHistoryRepository(db);
    service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasEditPermission: async () => true, hasExecutePermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    const branches = remoteGit('show-ref', '--heads'); const result = await deploy('v2');
    expect(candidate).toHaveLength(64); expect(result.commit).toBe(candidate);
    const prepared = await deployment.prepareDeployCandidate(repo, 'flow', candidate, candidate, 2, 2, author);
    expect((await deployment.resumePreparedDeployment(prepared, { remoteUrl: remote, username: author.name, email: author.email })).commit).toBe(candidate);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#2^{commit}')).toBe(candidate);
    expect(remoteGit('show-ref', '--heads')).toBe(branches);
  });

  it.each(['false dependency', 'large exact number'])('publishes a DB-matching legacy AutoHeal document without rewriting %s', async shape => {
    const document = shape === 'false dependency'
      ? { ...spec, nodes: [{ ...spec.nodes[0], dependsOn: false }, spec.nodes[1]] }
      : { ...spec, custom: 10000000000000000 };
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify(document));
    const yaml = '# exact legacy bytes\n' + stringify(document); write('workflows/flow.yaml', yaml); save();
    const branches = remoteGit('show-ref', '--heads'); const result = await deploy('autoheal');
    expect(result.commit).toBe(candidate); expect(remoteGit('show-ref', '--heads')).toBe(branches);
    const history = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(history.spec_json).content).toBe(yaml);
  });

  it('keeps Editor validation strict even when legacy false-dependency YAML exactly matches DB', async () => {
    const document = { ...spec, nodes: [{ ...spec.nodes[0], dependsOn: false }, spec.nodes[1]] };
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify(document));
    write('workflows/flow.yaml', stringify(document)); save(); const before = remoteGit('show-ref');
    await expect(deploy('v2')).rejects.toMatchObject({ code: 'INVALID_WORKFLOW' }); expect(remoteGit('show-ref')).toBe(before);
  });

  it('uses active history tag as production while wf already contains a newer draft', async () => {
    const production = candidate; activate(production); write('scripts/run.sh', 'new saved script\n'); save();
    expect(await service.editorDeploymentState('flow', 'actor')).toMatchObject({ savedCommit: candidate, activeCommit: production });
    const before = remoteGit('show-ref', '--heads'); const result = await deploy('v2', production);
    expect(result.deployNumber).toBe(2); expect(result.commit).toBe(candidate); expect(remoteGit('show-ref', '--heads')).toBe(before);
  });

  it('rechecks a configured host candidate policy before creating a tag', async () => {
    const history = new WorkflowDeployHistoryRepository(db);
    const authorize = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasEditPermission: async () => true, hasExecutePermission: async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author, undefined, authorize);
    const before = remoteGit('show-ref'); await expect(deploy('v2')).rejects.toMatchObject({ code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    expect(authorize).toHaveBeenCalledTimes(2); expect(remoteGit('show-ref')).toBe(before);
  });

  it.each(['v2', 'autoheal'])('rechecks revoked permissions before tag publication for %s', async route => {
    const history = new WorkflowDeployHistoryRepository(db);
    const grant = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      { hasEditPermission: route === 'v2' ? grant : async () => false,
        hasExecutePermission: route === 'autoheal' ? grant : async () => true } as never,
      { remoteUrl: remote, username: author.name, email: author.email }, author);
    const before = remoteGit('show-ref'); await expect(deploy(route)).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(grant).toHaveBeenCalledTimes(2); expect(remoteGit('show-ref')).toBe(before);
  });

  it('rejects an active-history race before tag publication without treating wf as a lease', async () => {
    const prepare = deployment.prepareDeployCandidate;
    vi.spyOn(deployment, 'prepareDeployCandidate').mockImplementationOnce(async (...args) => {
      const result = await prepare(...args);
      raw.prepare(`INSERT INTO workflow_deploy_history (pack_id,workflow_id,deploy_number,version,tag_name,action,spec_json,is_active)
        VALUES ('pack','flow',7,7,'deploy/flow/#7','deploy','{}',1)`).run();
      return result;
    });
    const before = remoteGit('show-ref'); await expect(deploy('v2')).rejects.toMatchObject({ code: 'DEPLOY_BASELINE_STALE' });
    expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT deploy_number,is_active FROM workflow_deploy_history').all()).toEqual([{ deploy_number: 7, is_active: 1 }]);
  });

  it('preserves a concurrent active history and reports unknown DB outcome after successful tag creation', async () => {
    const push = deployment.pushPreparedDeployment;
    vi.spyOn(deployment, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      const result = await push(...args);
      raw.prepare(`INSERT INTO workflow_deploy_history (pack_id,workflow_id,deploy_number,version,tag_name,action,spec_json,is_active)
        VALUES ('pack','flow',7,7,'deploy/flow/#7','deploy','{}',1)`).run();
      return result;
    });
    const branches = remoteGit('show-ref', '--heads');
    await expect(deploy('autoheal')).rejects.toMatchObject({ code: 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' });
    expect(remoteGit('show-ref', '--heads')).toBe(branches);
    expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{commit}')).toBe(candidate);
    expect(raw.prepare('SELECT deploy_number,is_active FROM workflow_deploy_history').all()).toEqual([{ deploy_number: 7, is_active: 1 }]);
  });

  it('compares production resources against active tag, not against current wf itself', async () => {
    write('scripts/production-only.sh', 'must survive\n'); const production = save(); activate(production);
    rmSync(path.join(repo, 'scripts/production-only.sh')); save(); const before = remoteGit('show-ref');
    await expect(deploy('v2', production)).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT', paths: ['scripts/production-only.sh'] });
    expect(remoteGit('show-ref')).toBe(before);
  });
});
