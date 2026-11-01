// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import type { WorkflowSpec } from '../../workflow.js';
import { parse as parseYaml } from 'yaml';
vi.setConfig({ testTimeout: 30000 });
const spec: WorkflowSpec = { id: 'flow', version: '1', title: 'New', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };
let root: string, remote: string, raw: Database.Database, db: SqliteDatabase;
const git = (...args: string[]) => runGit(root, args);
const refs = () => git('--git-dir', remote, 'show-ref');
const input = { workflowId: 'flow', actorId: 'owner', spec, createOnly: true, expectedSavedHead: null };
const service = () => new AutoHealSaveService(db, new WorkflowSpecRepository(db), null,
  new BotWorkflowPermissionRepository(db), { remoteUrl: remote, username: 'Fixture', email: 'fixture@example.invalid' },
  { name: 'Fixture', email: 'fixture@example.invalid' });
const counts = () => ['workflow_specs', 'bot_workflow_permissions', 'workflow_deploy_history']
  .map(table => (raw.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n);
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'create-safe-')); remote = createLocalRemote(root);
  raw = new Database(':memory:'); raw.exec(`
    CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT);`);
  db = new SqliteDatabase(raw);
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true }); });
describe('safe new workflow creation', () => {
  it('creates a unique workflow, verified owner grant and leased saved ref', async () => {
    expect(await service().save(input)).toEqual({ ok: true, saved: true, workflowId: 'flow', packId: 'flow', branch: 'saved/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
    expect(counts()).toEqual([1, 1, 0]);
    expect(await new BotWorkflowPermissionRepository(db).hasEditPermission('flow', 'owner')).toBe(true);
    expect(refs()).toContain('refs/heads/saved/flow'); expect(refs()).not.toContain('refs/heads/wf/flow');
    expect(git('--git-dir', remote, 'show', 'saved/flow:packs/flow/workflows/flow.yaml')).toContain('id: flow');
    await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
  });
  it.each(['wf', 'saved'])('rejects pre-existing %s ref without creating DB identity', async branch => {
    git('-C', path.join(root, 'seed'), 'push', remote, `HEAD:refs/heads/${branch}/flow`);
    const before = refs(); await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
    expect(counts()).toEqual([0, 0, 0]); expect(refs()).toBe(before);
  });
  it.each(['grant', 'history', 'workflow', 'pack'])('rejects existing DB %s identity', async kind => {
    if (kind === 'grant') raw.exec("INSERT INTO bot_workflow_permissions (workflow_id) VALUES ('flow')");
    if (kind === 'history') raw.exec("INSERT INTO workflow_deploy_history (workflow_id,pack_id) VALUES ('flow','other')");
    if (kind === 'workflow') raw.exec("INSERT INTO workflow_specs (workflow_id,pack_id) VALUES ('flow','other')");
    if (kind === 'pack') raw.exec("INSERT INTO workflow_specs (workflow_id,pack_id) VALUES ('other','flow')");
    const before = refs(), rows = counts();
    await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
    expect(counts()).toEqual(rows); expect(refs()).toBe(before);
  });
  it('does not adopt a default-branch pack or overwrite its workflow', async () => {
    const seed = path.join(root, 'seed'); mkdirSync(path.join(seed, 'packs/flow/workflows'), { recursive: true });
    writeFileSync(path.join(seed, 'packs/flow/workflow.pack.yaml'), 'id: flow\nversion: 1\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n');
    writeFileSync(path.join(seed, 'packs/flow/workflows/flow.yaml'), 'id: flow\nnodes: []\n');
    git('-C', seed, 'add', '.'); git('-C', seed, 'commit', '-m', 'existing'); git('-C', seed, 'push', remote, 'main');
    const before = refs(); await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
    expect(counts()).toEqual([0, 0, 0]); expect(refs()).toBe(before);
  });
  it('rolls back before pushing if the grant was ignored', async () => {
    raw.exec('CREATE TRIGGER ignored_grant BEFORE INSERT ON bot_workflow_permissions BEGIN SELECT RAISE(IGNORE); END');
    const before = refs(); await expect(service().save(input)).rejects.toThrow();
    expect(counts()).toEqual([0, 0, 0]); expect(refs()).toBe(before);
  });
  it('reports a lost push acknowledgement without blind retry', async () => {
    const original = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await original(args, options); if (args[0] === 'push') throw new Error('lost acknowledgement'); return result;
    });
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_OUTCOME_UNKNOWN', branch: 'saved/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
    expect(counts()).toEqual([0, 0, 0]); expect(refs()).toContain('refs/heads/saved/flow');
    vi.restoreAllMocks(); await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
  });
  it('reports unknown if the database commit acknowledgement is lost', async () => {
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementation(async fn => {
      await transaction(fn); throw new Error('lost database commit acknowledgement');
    });
    await expect(service().save(input)).rejects.toMatchObject({ code: 'CREATE_OUTCOME_UNKNOWN', branch: 'saved/flow' });
    expect(counts()).toEqual([1, 1, 0]); expect(refs()).toContain('refs/heads/saved/flow');
  });
  it('does not overwrite a saved ref created after the absence check', async () => {
    const original = gitExecution.executeGit;
    let injected = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push' && !injected) {
        injected = true; git('-C', path.join(root, 'seed'), 'push', remote, 'HEAD:refs/heads/saved/flow');
      }
      return original(args, options);
    });
    const initial = git('-C', path.join(root, 'seed'), 'rev-parse', 'HEAD');
    await expect(service().save(input)).rejects.toMatchObject({ code: 'CREATE_OUTCOME_UNKNOWN' });
    expect(git('--git-dir', remote, 'rev-parse', 'saved/flow')).toBe(initial);
    expect(counts()).toEqual([0, 0, 0]);
  });

  it.each([2, undefined])('preserves version and content in create and DB edit: %s', async version => {
    const changed = { ...spec, version, content: 'user content' } as unknown as WorkflowSpec;
    const expected = JSON.parse(JSON.stringify(changed));
    await service().save({ ...input, spec: changed });
    const saved = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json);
    expect(saved).toEqual(expected);
    const yaml = git('--git-dir', remote, 'show', 'saved/flow:packs/flow/workflows/flow.yaml');
    expect(parseYaml(yaml)).toEqual(expected);
    const edited = { ...changed, title: 'Edited', content: 'edited content' };
    await service().save({ workflowId: 'flow', actorId: 'owner', spec: edited });
    expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json)).toEqual(JSON.parse(JSON.stringify(edited)));
  });

});
