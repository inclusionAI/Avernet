// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import { assertLosslessJson } from '../lossless-json-validation.js';
import * as deployment from '../auto-heal-deploy-git.js';
import { runGit } from './clawflows-git-client-fixture.js';

const author = { name: 'Fixture', email: 'fixture@example.invalid' };
const spec = { id: 'flow', version: '1.0.0', title: 'Saved document', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };
const git = (cwd: string, ...args: string[]) => runGit(cwd, args);

function manifest(packId = 'pack', workflowId = 'flow') {
  return stringify({ id: packId, version: '1.0.0', workflows: [{ id: workflowId, file: `workflows/${workflowId}.yaml` }] });
}

describe('AutoHeal deployment without a Pack operation table', () => {
  let root: string, remote: string, work: string, candidate: string;
  let raw: Database.Database, db: SqliteDatabase, service: AutoHealDeployService;
  const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);

  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT DEFAULT 'test', gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
    raw.prepare('INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, owner_id) VALUES (?, ?, ?, ?, ?)')
      .run('flow', 'pack', JSON.stringify(spec), spec.title, 'user');
    raw.prepare('INSERT INTO bot_workflow_permissions (bot_owner_id, workflow_id, can_view, can_execute, can_edit) VALUES (?, ?, 1, 1, 0)')
      .run('user', 'flow');
    db = new SqliteDatabase(raw);
    root = mkdtempSync(path.join(tmpdir(), 'deploy-no-journal-')); remote = path.join(root, 'remote.git'); work = path.join(root, 'work');
    git(root, 'init', '--bare', '--initial-branch=main', remote); git(root, 'init', '--initial-branch=main', work);
    git(work, 'config', 'user.name', author.name); git(work, 'config', 'user.email', author.email);
    mkdirSync(path.join(work, 'workflows')); mkdirSync(path.join(work, 'scripts'));
    writeFileSync(path.join(work, 'workflow.pack.yaml'), manifest());
    writeFileSync(path.join(work, 'workflows/flow.yaml'), stringify(spec));
    writeFileSync(path.join(work, 'scripts/run.sh'), '#!/bin/sh\necho saved\n', { mode: 0o755 });
    writeFileSync(path.join(work, 'asset.bin'), Buffer.from([0, 1, 255, 10]));
    git(work, 'add', '.'); git(work, 'commit', '-m', 'saved Pack'); candidate = git(work, 'rev-parse', 'HEAD');
    git(work, 'remote', 'add', 'origin', remote);
    git(work, 'push', 'origin', `HEAD:refs/heads/main`, `HEAD:refs/heads/wf/flow`);
    const history = new WorkflowDeployHistoryRepository(db);
    service = new AutoHealDeployService(db, new WorkflowSpecRepository(db, history), history,
      new BotWorkflowPermissionRepository(db), { remoteUrl: remote, username: author.name, email: author.email }, author);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });

  const deploy = (extra: Record<string, unknown> = {}) => service.deploy({ workflowId: 'flow', actorId: 'user', candidateCommit: candidate, autoRun: false, ...extra });
  const writeCandidate = (yaml: string) => {
    writeFileSync(path.join(work, 'workflows/flow.yaml'), yaml);
    git(work, 'add', '.'); git(work, 'commit', '-m', 'wf candidate update');
    candidate = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
  };

  it('publishes the DB-matching complete Pack from wf, without a saved ref or branch mutation', async () => {
    const beforeBranches = remoteGit('show-ref', '--heads');
    const result = await deploy();
    expect(result).toMatchObject({ ok: true, deployed: true, packId: 'pack', deployNumber: 1, version: 1, candidateCommit: candidate, commit: candidate });
    expect(remoteGit('show-ref', '--heads')).toBe(beforeBranches);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(candidate);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(remoteGit('ls-tree', result.commit!, 'scripts/run.sh')).toMatch(/^100755 /);
    const row = raw.prepare('SELECT * FROM workflow_deploy_history').get() as { spec_json: string; is_active: number; tag_name: string };
    expect(JSON.parse(row.spec_json).content).toBe(stringify(spec));
    expect(row.tag_name).toBe(result.tagName);
    expect(row.is_active).toBe(1);
    expect(raw.prepare('SELECT version FROM workflow_specs').get()).toEqual({ version: 1 });
  });

  it('rejects a DB-only draft rather than overlaying it onto the immutable wf candidate', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?, title = ?').run(JSON.stringify({ ...spec, title: 'DB only draft' }), 'DB only draft');
    const before = remoteGit('show-ref');
    await expect(deploy()).rejects.toMatchObject({ status: 409, code: 'SNAPSHOT_DB_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('preserves legacy null dependencies and exact candidate YAML in history', async () => {
    const draft = { ...spec, custom: { dependsOn: null }, nodes: [{ ...spec.nodes[0], dependsOn: null }, spec.nodes[1]] };
    const draftJson = JSON.stringify(draft);
    const yaml = stringify(draft);
    raw.prepare('UPDATE workflow_specs SET spec_json = ?, title = ?').run(draftJson, draft.title);
    writeCandidate(yaml);
    const result = await deploy();
    expect(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).toBe(yaml.trimEnd());
    const row = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(JSON.parse(row.spec_json).content).toBe(yaml);
    expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json)).toEqual(draft);
  });

  it('allows a content-only legacy wrapper only when the candidate has matching semantics', async () => {
    const draft = { ...spec, title: 'Legacy content', nodes: [{ ...spec.nodes[0], dependsOn: null }, spec.nodes[1]] };
    const yaml = stringify(draft);
    raw.prepare('UPDATE workflow_specs SET spec_json = ?, title = ?').run(JSON.stringify({ content: yaml }), draft.title);
    writeCandidate(yaml);
    const result = await deploy();
    expect(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).toBe(yaml.trimEnd());
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  });

  it('rejects a content-only DB wrapper when saved YAML is semantically different', async () => {
    const dbYaml = stringify({ ...spec, title: 'Database only' });
    raw.prepare('UPDATE workflow_specs SET spec_json = ?, title = ?').run(JSON.stringify({ content: dbYaml }), 'Database only');
    const before = remoteGit('show-ref');
    await expect(deploy()).rejects.toMatchObject({ status: 409, code: 'SNAPSHOT_DB_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });

  it('rejects lossy JSON and unsafe numeric values before any publication', async () => {
    const invalid = [
      '{"id":"flow","version":"1.0.0","title":"Saved document","nodes":[],"custom":0.10000000000000001}',
      '{"id":"flow","version":"1.0.0","title":"Saved document","nodes":[],"custom":9007199254740993}',
      '{"id":"flow","version":"1.0.0","title":"Saved document","nodes":[],"custom":{"key":1,"key":2}}',
      '{"id":"flow","version":"1.0.0","title":"Saved document","nodes":[],"custom":-0}',
    ];
    for (const text of invalid) {
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
      const before = remoteGit('show-ref');
      await expect(deploy()).rejects.toMatchObject({ status: 400, code: 'INVALID_WORKFLOW' });
      expect(remoteGit('show-ref')).toBe(before);
      expect((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json).toBe(text);
    }
  });

  it('keeps exact JSON numbers and null dependencies when the wf candidate matches', async () => {
    const draft = { ...spec, nodes: [{ ...spec.nodes[0], dependsOn: null }, spec.nodes[1]], custom: {
      fraction: 0.1, integer: 9007199254740991, exponent: 1.25e-8, zero: 0,
      quoted: '0.10000000000000001 "key":1, "key":2', left: { key: 1 }, right: { key: 2 },
    } };
    const text = JSON.stringify(draft).replace('1.25e-8', '1.2500e-8');
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
    writeCandidate(stringify(draft));
    const result = await deploy();
    expect(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).toBe(stringify(draft).trimEnd());
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  });

  it('keeps execute-only AutoHeal authorization and rejects an unauthorized actor', async () => {
    await expect(service.deploy({ workflowId: 'flow', actorId: 'other', candidateCommit: candidate, autoRun: false }))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    await expect(deploy({ candidateCommit: 'f'.repeat(40) })).rejects.toMatchObject({ status: 409, code: 'CANDIDATE_NOT_SAVED' });
    expect(remoteGit('tag', '-l')).toBe('');
  });

  it('rejects a null Pack identity that does not match the candidate manifest', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL').run();
    const before = remoteGit('show-ref');
    await expect(deploy()).rejects.toMatchObject({ status: 409, code: 'PACK_ID_MISMATCH' });
    expect(remoteGit('show-ref')).toBe(before);
  });

  it('requires wf and never falls back to main or another workflow ref', async () => {
    remoteGit('update-ref', '-d', 'refs/heads/wf/flow');
    remoteGit('update-ref', 'refs/heads/wf/other', candidate);
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false }))
      .rejects.toMatchObject({ status: 409, code: 'SAVE_REQUIRED' });
    expect(remoteGit('tag', '-l')).toBe('');
  });

  it('does not CAS or replace wf when it advances after preparation', async () => {
    const realPush = deployment.pushPreparedDeployment;
    vi.spyOn(deployment, 'pushPreparedDeployment').mockImplementationOnce(async (...args) => {
      writeFileSync(path.join(work, 'asset.bin'), Buffer.from([9, 8, 7]));
      git(work, 'add', '.'); git(work, 'commit', '-m', 'newer wf draft');
      const newer = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', `HEAD:refs/heads/wf/flow`);
      const result = await realPush(...args);
      expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(newer);
      return result;
    });
    const result = await deploy();
    expect(result.commit).toBe(candidate);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(candidate);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  });

  it('reports an uncertain push without recording a deployment', async () => {
    vi.spyOn(deployment, 'pushPreparedDeployment').mockRejectedValueOnce(new Error('network lost'));
    await expect(deploy({ requestId: 'no-journal-unknown' })).rejects.toMatchObject({
      status: 503, code: 'DEPLOY_OUTCOME_UNKNOWN', requestId: 'no-journal-unknown', tagName: 'deploy/flow/#1',
    });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
});

describe('lossless raw JSON resource bounds', () => {
  it('handles long almost-zero tails in linear time and preserves decimal correctness', () => {
    const zeros = '0'.repeat(140_000);
    const start = performance.now();
    expect(() => assertLosslessJson(`{"n":0.1${zeros}1}`)).toThrow();
    expect(() => assertLosslessJson(`{"n":0.1${zeros}}`)).not.toThrow();
    expect(performance.now() - start).toBeLessThan(3500);
  }, 30_000);

  it('allows a 1 MiB YAML string even when each byte is JSON-unicode-escaped', () => {
    expect(() => assertLosslessJson(`{"content":"${'\\u0061'.repeat(1024 * 1024)}"}`)).not.toThrow();
  }, 30_000);

  it('bounds raw JSON bytes and the number of values', () => {
    const maxBytes = 8 * 1024 * 1024;
    expect(() => assertLosslessJson(`${' '.repeat(maxBytes - 2)}{}`)).not.toThrow();
    expect(() => assertLosslessJson(`${' '.repeat(maxBytes - 1)}{}`)).toThrow();
    expect(() => assertLosslessJson(`"${'é'.repeat(maxBytes / 2)}"`)).toThrow();
    const maxValues = 1024 * 1024;
    const body = 'null,'.repeat(maxValues - 2);
    expect(() => assertLosslessJson(`[${body}null]`)).not.toThrow();
    expect(() => assertLosslessJson(`[${body}null,null]`)).toThrow();
  }, 30_000);
});
