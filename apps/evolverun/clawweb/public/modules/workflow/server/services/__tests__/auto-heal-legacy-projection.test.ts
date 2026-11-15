// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { parse, stringify } from 'yaml';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import type { WorkflowSpec } from '../../workflow.js';
import { editorDraftToken } from '../editor-draft-token.js';
import { runGit } from './clawflows-git-client-fixture.js';

const author = { name: 'Test', email: 'test@example.invalid' };
const yaml = 'id: flow\ntitle: Legacy\ncustom: &user { message: hello }\ncopied: *user\ncontent: authored\nnodes:\n  - id: start\n    executor: { type: start }\n    dependsOn: false\n';
const git = (cwd: string, ...args: string[]) => runGit(cwd, args);

describe('legacy AutoHeal projection', () => {
  let root: string, remote: string, db: SqliteDatabase, raw: Database.Database;
  let save: AutoHealSaveService, deploy: AutoHealDeployService;
  beforeEach(async () => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT DEFAULT 'test', gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
    db = new SqliteDatabase(raw);
    const history = new WorkflowDeployHistoryRepository(db), specs = new WorkflowSpecRepository(db, history), permissions = new BotWorkflowPermissionRepository(db);
    await specs.upsert('flow', 'pack', JSON.stringify({ id: 'flow', nodes: [{ id: 'start', executor: { type: 'start' } }] }), 'user');
    await permissions.upsert({ bot_id: null, bot_owner_id: 'user', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 1 });
    root = mkdtempSync(path.join(tmpdir(), 'legacy-projection-')); remote = path.join(root, 'remote.git'); const work = path.join(root, 'work');
    git(root, 'init', '--bare', '--initial-branch=main', remote); git(root, 'init', '--initial-branch=main', work);
    git(work, 'config', 'user.name', author.name); git(work, 'config', 'user.email', author.email);
    mkdirSync(path.join(work, 'workflows'));
    writeFileSync(path.join(work, 'workflow.pack.yaml'), stringify({ id: 'pack', version: '1', workflows: [{ id: 'flow', file: 'workflows/flow.yaml' }] }));
    writeFileSync(path.join(work, 'workflows/flow.yaml'), yaml);
    git(work, 'add', '.'); git(work, 'commit', '-m', 'saved'); git(work, 'remote', 'add', 'origin', remote);
    git(work, 'push', 'origin', 'HEAD:refs/heads/main', 'HEAD:refs/heads/wf/flow');
    const config = { remoteUrl: remote, username: author.name, email: author.email, token: '' };
    save = new AutoHealSaveService(db, specs, history, permissions, config, author);
    deploy = new AutoHealDeployService(db, specs, history, permissions, config, author);
  });
  function syncLegacyProjection(specJson: unknown, content: string): void {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify(specJson));
    writeFileSync(path.join(root, 'work', 'workflows/flow.yaml'), content);
    const work = path.join(root, 'work');
    git(work, 'add', 'workflows/flow.yaml');
    if (git(work, 'status', '--short')) git(work, 'commit', '-m', 'sync legacy workflow baseline');
    git(work, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
  }
  afterEach(async () => { await db.close(); rmSync(root, { recursive: true, force: true }); });
  it('saves and deploys a legacy false dependency and preserves authored fields', async () => {
    const document = parse(yaml) as WorkflowSpec;
    await save.save({ workflowId: 'flow', actorId: 'user', spec: document });
    const result = await deploy.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    expect(parse(gitYaml)).toEqual(document);
    const row = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    const history = JSON.parse(row.spec_json);
    expect(history.content).toBe(gitYaml);
    expect(parse(history.content)).toEqual(document);
  });
  it.each([
    ['false dependency', JSON.stringify(parse(yaml))],
    ['alias wrapper', JSON.stringify({ content: yaml.replace('dependsOn: false', 'dependsOn: []') })],
  ])('keeps Editor deployment strict for %s', async (_label, draft) => {
    const parsed = JSON.parse(draft) as Record<string, unknown>;
    syncLegacyProjection(parsed, typeof parsed.content === 'string' && !Array.isArray(parsed.nodes) ? parsed.content : yaml);
    const specs = new WorkflowSpecRepository(db, new WorkflowDeployHistoryRepository(db));
    const row = await specs.findByWorkflowId('flow');
    if (!row) throw new Error('Missing draft');
    const before = git(root, '--git-dir', remote, 'show-ref');
    // Supply the mandatory non-v2 Editor host policy so this test reaches
    // strict document validation rather than stopping at the authorization gate.
    const editor = new AutoHealDeployService(db, specs, new WorkflowDeployHistoryRepository(db),
      new BotWorkflowPermissionRepository(db), { remoteUrl: remote, username: author.name, email: author.email }, author,
      undefined, async () => true);
    await expect(editor.editorDeploy({ workflowId: 'flow', actorId: 'user', autoRun: false,
      expectedDraftToken: editorDraftToken(row) })).rejects.toMatchObject({ code: 'INVALID_WORKFLOW' });
    expect(git(root, '--git-dir', remote, 'show-ref')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('denies non-v2 Editor deployment without the host candidate policy', async () => {
    const row = await new WorkflowSpecRepository(db, new WorkflowDeployHistoryRepository(db)).findByWorkflowId('flow');
    if (!row) throw new Error('Missing draft');
    const before = git(root, '--git-dir', remote, 'show-ref');
    await expect(deploy.editorDeploy({ workflowId: 'flow', actorId: 'user', autoRun: false,
      expectedDraftToken: editorDraftToken(row) })).rejects.toMatchObject({ status: 403, code: 'SNAPSHOT_DEPLOY_FORBIDDEN' });
    expect(git(root, '--git-dir', remote, 'show-ref')).toBe(before);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
  });
  it('deploys an existing content-only wrapper with aliases and exact YAML bytes', async () => {
    syncLegacyProjection({ content: yaml }, yaml);
    const result = await deploy.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    const row = raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string };
    expect(gitYaml).toBe(yaml);
    expect(JSON.parse(row.spec_json).content).toBe(yaml);
    expect(parse(JSON.parse(row.spec_json).content)).toEqual(parse(yaml));
  });
  it('saves and deploys an exactly round-trippable large integer and numeric configuration key', async () => {
    const document = { ...parse(yaml), config: { 200: 'ok' }, large: 10000000000000000 } as WorkflowSpec;
    await save.save({ workflowId: 'flow', actorId: 'user', spec: document });
    const result = await deploy.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    expect(parse(gitYaml)).toEqual(document);
    const history = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string }).spec_json);
    expect(history.content).toBe(gitYaml);
    expect(history.large).toBe(10000000000000000);
    expect(history.config).toEqual({ '200': 'ok' });
  });
  it('deploys tagged strings and numeric keys in a legacy content-only wrapper', async () => {
    const document = 'id: flow\nconfig: {200: ok}\nlabel: !!str 200\nlarge: 10000000000000000\nnodes:\n  - id: start\n    executor: {type: start}\n';
    syncLegacyProjection({ content: document }, document);
    const result = await deploy.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    const history = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string }).spec_json);
    expect(gitYaml).toBe(document);
    expect(history.content).toBe(document);
    expect(history.label).toBe('200');
    expect(history.config).toEqual({ '200': 'ok' });
  });
  it('deploys and archives lossless standard YAML tags in a legacy content wrapper', async () => {
    const document = 'id: flow\nconfig: !!map { retries: !!int 2, rate: !!float 0.5, enabled: !!bool true, values: !!seq [a, b] }\nnodes:\n  - id: start\n    executor: {type: start}\n';
    syncLegacyProjection({ content: document }, document);
    const result = await deploy.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8' });
    const history = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string }).spec_json);
    expect(gitYaml).toBe(document);
    expect(history.content).toBe(document);
    expect(history.config).toEqual({ retries: 2, rate: 0.5, enabled: true, values: ['a', 'b'] });
  });
  it('deploys a legacy YAML whose escaped JSON representation exceeds 1 MiB', async () => {
    const document = `id: flow\nnodes:\n  - id: start\n    executor: {type: start}\ntext: '${'\\'.repeat(550_000)}'\n`;
    syncLegacyProjection({ content: document }, document);
    const result = await deploy.deploy({ workflowId: 'flow', actorId: 'user', autoRun: false });
    const gitYaml = execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:workflows/flow.yaml`], { encoding: 'utf8', maxBuffer: 3 * 1024 * 1024 });
    const history = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_deploy_history').get() as { spec_json: string }).spec_json);
    expect(gitYaml).toBe(document);
    expect(history.content).toBe(document);
    expect(history.text).toBe('\\'.repeat(550_000));
  });

});
