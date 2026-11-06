// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import express from 'express';
import { parse, stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createWorkflowsRouter } from '../../routes/workflows.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import { editorDraftToken } from '../editor-draft-token.js';
import { assertLosslessJson } from '../lossless-json-validation.js';
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
  const readHistory = async (deployNumber: number) => {
    const app = express();
    app.use('/api/workflows', createWorkflowsRouter(null, null, new BotWorkflowPermissionRepository(db), null, new WorkflowDeployHistoryRepository(db)));
    const server = app.listen(0, '127.0.0.1');
    try {
      await new Promise<void>((resolve, reject) => { server.once('listening', resolve); server.once('error', reject); });
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('Missing history listener');
      const response = await fetch(`http://127.0.0.1:${address.port}/api/workflows/flow/history/by-deploy/${deployNumber}`,
        { headers: { 'X-Staff-Id': 'user' } });
      expect(response.status).toBe(200);
      return await response.json() as { specJson: string };
    } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
  };
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
  it('deploys legacy null dependencies without changing the archived YAML or authored fields', async () => {
    const draft = { ...spec, content: { owner: 'user' }, custom: { dependsOn: null },
      nodes: [{ ...spec.nodes[0], dependsOn: null }, spec.nodes[1]] };
    const draftJson = JSON.stringify(draft);
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(draftJson);
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(result).toMatchObject({ ok: true, deployed: true });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    expect(gitYaml).toBe(stringify(draft));
    const history = JSON.parse((await readHistory(result.deployNumber)).specJson);
    expect(history.content).toBe(gitYaml);
    expect(parse(history.content)).toEqual(draft);
    expect(history.nodes[0].dependsOn).toBeNull();
    expect(history.custom).toEqual(draft.custom);
    expect((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json).toBe(draftJson);
  });
  it('rejects an unsafe number alongside legacy null before publishing', async () => {
    const yaml = 'id: flow\nnodes:\n  - id: start\n    executor: { type: start }\n    dependsOn: null\nlarge: 9007199254740993\n';
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ content: yaml }));
    const before = remoteGit('show-ref');
    await expect(service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false }))
      .rejects.toMatchObject({ code: 'INVALID_WORKFLOW' });
    expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  const deployDraft = async (editor: boolean) => {
    const input = { workflowId: 'flow', actorId: 'user', autoRun: false };
    if (!editor) return service.deploy(input);
    // These document-validation cases need to pass the real non-v2 Editor
    // edit and candidate-policy gates. The default legacy fixture remains
    // execute-only so legacy AutoHeal coverage is unchanged.
    const permissions = new BotWorkflowPermissionRepository(db);
    await permissions.upsert({ bot_id: null, bot_owner_id: 'user', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 1 });
    const history = new WorkflowDeployHistoryRepository(db), specs = new WorkflowSpecRepository(db, history);
    const row = await specs.findByWorkflowId('flow');
    if (!row) throw new Error('Missing draft');
    const editorService = new AutoHealDeployService(db, specs, history, permissions,
      { remoteUrl: remote, username: author.name, email: author.email, token: '' }, author, undefined, async () => true);
    return editorService.editorDeploy({ ...input, expectedDraftToken: editorDraftToken(row) });
  };
  it.each([false, true])('rejects lossy raw JSON and duplicate keys before any publication (editor=%s)', async editor => {
    const base = JSON.stringify(spec).slice(0, -1);
    for (const extra of [
      ',"custom":0.10000000000000001}', ',"custom":9007199254740993}',
      ',"custom":1e-400}', ',"custom":-0}', ',"custom":1e400}',
      ',"custom":{"key":1,"key":2}}', ',"custom":{"key":1,"\\u006bey":2}}',
      ',"id":"flow"}',
    ]) {
      const text = base + extra;
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
      const refs = remoteGit('show-ref');
      await expect(deployDraft(editor)).rejects.toMatchObject({ status: 400, code: 'INVALID_WORKFLOW' });
      expect(remoteGit('show-ref')).toBe(refs);
      expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
      expect((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json).toBe(text);
    }
  });
  it.each([false, true])('deploys lossless JSON numbers and null dependencies (editor=%s)', async editor => {
    const text = JSON.stringify({ ...spec, nodes: [{ ...spec.nodes[0], dependsOn: null }, spec.nodes[1]],
      custom: { fraction: 0.1, integer: 9007199254740991, exponent: 1.25e-8, zero: 0,
        quoted: '0.10000000000000001 \"key\":1, \"key\":2', left: { key: 1 }, right: { key: 2 } } })
      .replace('1.25e-8', '1.2500e-8');
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
    const result = await deployDraft(editor);
    const yaml = remoteGit('show', `${result.commit}:workflows/flow.yaml`);
    expect(parse(yaml)).toEqual(JSON.parse(text));
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(result.commit);
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  });
  it.each([false, true])('deploys a content-only legacy wrapper with null dependencies (editor=%s)', async editor => {
    const legacyYaml = stringify({ ...spec, nodes: [{ ...spec.nodes[0], dependsOn: null }, spec.nodes[1]] });
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify({ content: legacyYaml }));
    const result = await deployDraft(editor);
    expect(remoteGit('show', `${result.commit}:workflows/flow.yaml`)).toBe(legacyYaml.trimEnd());
    expect((raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get())).toEqual({ n: 1 });
  });
  it('rejects oversized and over-populated drafts before changing Git or history', async () => {
    const refs = remoteGit('show-ref');
    const base = JSON.stringify(spec).slice(0, -1);
    const drafts = [
      `${base},"padding":"${'a'.repeat(8 * 1024 * 1024)}"}`,
      `${base},"items":[${'null,'.repeat(1024 * 1024)}null]}`,
    ];
    for (const [index, text] of drafts.entries()) {
      raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
      await expect(deployDraft(index === 1)).rejects.toMatchObject({ status: 400, code: 'INVALID_WORKFLOW' });
      expect(remoteGit('show-ref')).toBe(refs);
      expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    }
  }, 30_000);
  it('deploys a 1 MiB legacy YAML wrapper encoded with JSON unicode escapes', async () => {
    const prefix = `${stringify(spec)}#`;
    const padding = 1024 * 1024 - Buffer.byteLength(prefix);
    const escapedPrefix = Array.from(prefix, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`).join('');
    const text = `{"content":"${escapedPrefix}${'\\u0061'.repeat(padding)}"}`;
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(text);
    const result = await deployDraft(false);
    expect(result).toMatchObject({ ok: true, deployed: true });
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(result.commit);
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 1 });
  }, 30_000);
  it('denies non-v2 Editor deployment without edit permission or an approving host policy', async () => {
    const history = new WorkflowDeployHistoryRepository(db), specs = new WorkflowSpecRepository(db, history);
    const row = await specs.findByWorkflowId('flow'); if (!row) throw new Error('Missing draft');
    const input = { workflowId: 'flow', actorId: 'user', autoRun: false, expectedDraftToken: editorDraftToken(row) };
    const before = remoteGit('show-ref');
    await expect(service.editorDeploy(input)).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    const permissions = new BotWorkflowPermissionRepository(db);
    await permissions.upsert({ bot_id: null, bot_owner_id: 'user', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 1 });
    await expect(service.editorDeploy(input)).rejects.toMatchObject({ status: 403, code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    const policy = vi.fn(async () => false);
    const denied = new AutoHealDeployService(db, specs, history, permissions,
      { remoteUrl: remote, username: author.name, email: author.email, token: '' }, author, undefined, policy);
    await expect(denied.editorDeploy(input)).rejects.toMatchObject({ status: 403, code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    expect(policy).toHaveBeenCalledOnce(); expect(remoteGit('show-ref')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
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
  it.each([
    ['plain string', 'user payload'],
    ['YAML-like string', stringify(spec)],
    ['empty string', ''],
    ['object', { nested: ['kept', 0, null] }],
    ['null', null],
    ['false', false],
  ])('preserves authored content (%s) through real deploy and history reads', async (_label, content) => {
    const draft = { ...spec, content };
    const draftJson = JSON.stringify(draft);
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(draftJson);
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const yaml = stringify(draft);
    expect(remoteGit('rev-parse', `refs/tags/${result.tagName}^{commit}`)).toBe(result.commit);
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    expect(gitYaml).toBe(yaml);
    expect(parse(remoteGit('show', `${result.commit}:workflows/flow.yaml`))).toEqual(draft);
    const history = await readHistory(result.deployNumber);
    const archived = JSON.parse(history.specJson);
    expect(archived.content).toBe(gitYaml);
    expect(parse(archived.content)).toEqual(draft);
    expect((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json).toBe(draftJson);
  });
  it('keeps legacy no-content history readable and retains the no-content deployment projection', async () => {
    const legacy = JSON.stringify(spec);
    raw.prepare(`INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, action, spec_json, is_active)
      VALUES ('pack', 'flow', 1, 1, 'deploy', ?, 0)`).run(legacy);
    const result = await service.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    expect(JSON.parse((await readHistory(1)).specJson)).toEqual(spec);
    const deployed = JSON.parse((await readHistory(result.deployNumber)).specJson);
    expect(parse(deployed.content)).toEqual({ ...spec, title: 'Database YAML' });
    expect(deployed.id).toBe('flow');
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

// These parser regressions do not need the SQLite/Git fixture above.
describe('lossless raw JSON resource bounds', () => {
  it('handles long almost-zero tails in linear time and preserves decimal correctness', () => {
    const zeros = '0'.repeat(140_000);
    const start = performance.now();
    expect(() => assertLosslessJson(`{"n":0.1${zeros}1}`)).toThrow();
    expect(() => assertLosslessJson(`{"n":0.1${zeros}}`)).not.toThrow();
    expect(performance.now() - start).toBeLessThan(3500);
  }, 30_000);

  it('allows a 1 MiB YAML string even when each byte is JSON-unicode-escaped', () => {
    const json = `{"content":"${'\\u0061'.repeat(1024 * 1024)}"}`;
    expect(() => assertLosslessJson(json)).not.toThrow();
  }, 30_000);

  it('bounds raw JSON bytes and the number of values', () => {
    const maxBytes = 8 * 1024 * 1024;
    expect(() => assertLosslessJson(`${' '.repeat(maxBytes - 2)}{}`)).not.toThrow();
    expect(() => assertLosslessJson(`${' '.repeat(maxBytes - 1)}{}`)).toThrow();
    expect(() => assertLosslessJson(`\"${'é'.repeat(maxBytes / 2)}\"`)).toThrow();
    const maxValues = 1024 * 1024;
    const body = 'null,'.repeat(maxValues - 2);
    expect(() => assertLosslessJson(`[${body}null]`)).not.toThrow();
    expect(() => assertLosslessJson(`[${body}null,null]`)).toThrow();
  }, 30_000);
});
