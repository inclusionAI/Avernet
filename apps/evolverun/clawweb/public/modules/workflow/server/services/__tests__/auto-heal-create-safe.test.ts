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
  raw = new Database(path.join(root, 'workflow.sqlite')); raw.exec(`
    CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT);`);
  db = new SqliteDatabase(raw);
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true }); });
describe('safe new workflow creation', () => {
  it('creates a unique workflow, verified owner grant and leased wf ref', async () => {
    expect(await service().save(input)).toEqual({ ok: true, saved: true, workflowId: 'flow', packId: 'flow', branch: 'wf/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
    expect(counts()).toEqual([1, 1, 0]);
    expect(await new BotWorkflowPermissionRepository(db).hasEditPermission('flow', 'owner')).toBe(true);
    expect(refs()).toContain('refs/heads/wf/flow'); expect(refs()).not.toContain('refs/heads/saved/flow');
    expect(git('--git-dir', remote, 'show', 'wf/flow:packs/flow/workflows/flow.yaml')).toContain('id: flow');
    await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
  });
  it('rejects a pre-existing wf ref without creating DB identity', async () => {
    const branch = 'wf';
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
  it('does not adopt or overwrite a default-branch Pack when creating a new wf', async () => {
    const seed = path.join(root, 'seed'); mkdirSync(path.join(seed, 'packs/flow/workflows'), { recursive: true });
    writeFileSync(path.join(seed, 'packs/flow/workflow.pack.yaml'), 'id: flow\nversion: 1\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'packs/flow/workflows/flow.yaml'), 'id: flow\nnodes: []\n');
    writeFileSync(path.join(seed, 'packs/flow/private.txt'), 'not the new workflow');
    git('-C', seed, 'add', '.'); git('-C', seed, 'commit', '-m', 'unrelated default Pack'); git('-C', seed, 'push', remote, 'main');
    const defaultHead = git('--git-dir', remote, 'rev-parse', 'refs/heads/main');
    const result = await service().save(input);
    expect(counts()).toEqual([1, 1, 0]); expect(git('--git-dir', remote, 'rev-parse', 'refs/heads/main')).toBe(defaultHead);
    expect(git('--git-dir', remote, 'show', '-s', '--format=%P', result.commit!)).toBe('');
    expect(git('--git-dir', remote, 'ls-tree', '-r', '--name-only', result.commit!)).not.toContain('private.txt');
  });
  it('ignores a saved-only ref without adopting or changing its objects', async () => {
    const old = git('-C', path.join(root, 'seed'), 'rev-parse', 'HEAD');
    git('-C', path.join(root, 'seed'), 'push', remote, `${old}:refs/heads/saved/flow`);
    const result = await service().save(input);
    expect(git('--git-dir', remote, 'rev-parse', 'refs/heads/saved/flow')).toBe(old);
    expect(git('--git-dir', remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
    expect(git('--git-dir', remote, 'show', '-s', '--format=%P', result.commit!)).toBe('');
  });
  it('commits the workflow and grant before starting Git push', async () => {
    const original = gitExecution.executeGit;
    let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') {
        pushes++;
        expect(raw.inTransaction).toBe(false);
        const observer = new Database(path.join(root, 'workflow.sqlite'), { readonly: true });
        try {
          expect((observer.prepare('SELECT COUNT(*) AS n FROM workflow_specs').get() as { n: number }).n).toBe(1);
          expect((observer.prepare('SELECT COUNT(*) AS n FROM bot_workflow_permissions').get() as { n: number }).n).toBe(1);
        } finally { observer.close(); }
      }
      return original(args, options);
    });
    await service().save(input);
    expect(pushes).toBe(1);
  });
  it('does not push when the DB transaction rolls back after its callback', async () => {
    const original = gitExecution.executeGit;
    let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') pushes++;
      return original(args, options);
    });
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementation(fn => transaction(async tx => {
      await fn(tx); throw new Error('database commit failed');
    }));
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_DB_OUTCOME_UNKNOWN', branch: undefined, commit: undefined });
    expect(pushes).toBe(0); expect(counts()).toEqual([0, 0, 0]);
    expect(refs()).not.toContain('refs/heads/wf/flow');
  });
  it.each(['push failed', 'push timed out'])('keeps the committed database draft when %s', async reason => {
    const original = gitExecution.executeGit;
    let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') { pushes++; throw new Error(reason); }
      return original(args, options);
    });
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', branch: 'wf/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
    expect(pushes).toBe(1); expect(counts()).toEqual([1, 1, 0]);
    expect(refs()).not.toContain('refs/heads/wf/flow');
  });
  it('reports an unknown Git outcome when helper validation fails before any push command', async () => {
    const original = gitExecution.executeGit, transaction = db.transaction.bind(db);
    let committed = false, validationFailed = false, pushes = 0;
    vi.spyOn(db, 'transaction').mockImplementation(async fn => {
      const result = await transaction(fn); committed = true; return result;
    });
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') pushes++;
      if (committed && args[0] === 'rev-parse' && args[1] === '--verify' && args[2]?.endsWith('^{commit}')) {
        validationFailed = true; throw new Error('candidate validation failed');
      }
      return original(args, options);
    });
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN',
      branch: 'wf/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
    expect(validationFailed).toBe(true); expect(pushes).toBe(0);
    expect(counts()).toEqual([1, 1, 0]); expect(refs()).not.toContain('refs/heads/wf/flow');
  });
  it.each([
    ['spec', "UPDATE workflow_specs SET spec_json = '{\"id\":\"flow\",\"title\":\"other\"}' WHERE workflow_id = 'flow'"],
    ['pack identity', "UPDATE workflow_specs SET pack_id = 'other' WHERE workflow_id = 'flow'"],
    ['row identity', "UPDATE workflow_specs SET id = id + 100 WHERE workflow_id = 'flow'"],
    ['workflow identity', "UPDATE workflow_specs SET workflow_id = 'other' WHERE workflow_id = 'flow'"],
    ['owner', "UPDATE workflow_specs SET owner_id = 'other' WHERE workflow_id = 'flow'"],
    ['deleted draft', "DELETE FROM workflow_specs WHERE workflow_id = 'flow'"],
    ['revoked edit grant', "UPDATE bot_workflow_permissions SET can_edit = 0 WHERE workflow_id = 'flow'"],
  ])('does not push if %s changes after DB commit', async (_label, sql) => {
    const transaction = db.transaction.bind(db), original = gitExecution.executeGit;
    let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') pushes++;
      return original(args, options);
    });
    vi.spyOn(db, 'transaction').mockImplementation(async fn => {
      const result = await transaction(fn);
      const concurrent = new Database(path.join(root, 'workflow.sqlite'));
      try { concurrent.exec(sql); } finally { concurrent.close(); }
      return result;
    });
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_DB_CONFIRMED_GIT_NOT_STARTED', branch: undefined, commit: undefined });
    expect(pushes).toBe(0); expect(refs()).not.toContain('refs/heads/wf/flow');
    expect(counts()).toEqual([_label === 'deleted draft' ? 0 : 1, 1, 0]);
    if (_label === 'spec') expect((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json).toContain('other');
    if (_label === 'revoked edit grant') expect((raw.prepare('SELECT can_edit FROM bot_workflow_permissions').get() as { can_edit: number }).can_edit).toBe(0);
  });
  it.each(['snapshot read', 'permission read'])('does not push if the post-commit %s fails', async kind => {
    const transaction = db.transaction.bind(db), original = gitExecution.executeGit;
    let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') pushes++;
      return original(args, options);
    });
    vi.spyOn(db, 'transaction').mockImplementation(async fn => {
      const result = await transaction(fn);
      if (kind === 'snapshot read') vi.spyOn(db, 'query').mockRejectedValue(new Error('database unavailable'));
      else vi.spyOn(BotWorkflowPermissionRepository.prototype, 'hasEditPermission').mockRejectedValue(new Error('permission unavailable'));
      return result;
    });
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_DB_CONFIRMED_GIT_NOT_STARTED' });
    expect(pushes).toBe(0); expect(counts()).toEqual([1, 1, 0]); expect(refs()).not.toContain('refs/heads/wf/flow');
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
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', branch: 'wf/flow', commit: expect.stringMatching(/^[a-f0-9]{40,64}$/) });
    expect(counts()).toEqual([1, 1, 0]); expect(refs()).toContain('refs/heads/wf/flow');
    vi.restoreAllMocks(); await expect(service().save(input)).rejects.toMatchObject({ code: 'WORKFLOW_ALREADY_EXISTS' });
  });
  it('reports unknown if the database commit acknowledgement is lost', async () => {
    const original = gitExecution.executeGit;
    let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') pushes++;
      return original(args, options);
    });
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementation(async fn => {
      await transaction(fn); throw new Error('lost database commit acknowledgement');
    });
    await expect(service().save(input)).rejects.toMatchObject({ code: 'CREATE_DB_OUTCOME_UNKNOWN', branch: undefined, commit: undefined });
    expect(pushes).toBe(0); expect(counts()).toEqual([1, 1, 0]); expect(refs()).not.toContain('refs/heads/wf/flow');
  });
  it('does not overwrite a wf ref created after the absence check', async () => {
    const original = gitExecution.executeGit;
    let injected = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push' && !injected) {
        injected = true; git('-C', path.join(root, 'seed'), 'push', remote, 'HEAD:refs/heads/wf/flow');
      }
      return original(args, options);
    });
    const initial = git('-C', path.join(root, 'seed'), 'rev-parse', 'HEAD');
    await expect(service().save(input)).rejects.toMatchObject({ code: 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN' });
    expect(git('--git-dir', remote, 'rev-parse', 'wf/flow')).toBe(initial);
    expect(counts()).toEqual([1, 1, 0]);
  });

  it.each([2, undefined])('preserves version and content in create and DB edit: %s', async version => {
    const changed = { ...spec, version, content: 'user content' } as unknown as WorkflowSpec;
    const expected = JSON.parse(JSON.stringify(changed));
    await service().save({ ...input, spec: changed });
    const saved = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json);
    expect(saved).toEqual(expected);
    const yaml = git('--git-dir', remote, 'show', 'wf/flow:packs/flow/workflows/flow.yaml');
    expect(parseYaml(yaml)).toEqual(expected);
    const edited = { ...changed, title: 'Edited', content: 'edited content' };
    await service().save({ workflowId: 'flow', actorId: 'owner', spec: edited });
    expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs').get() as { spec_json: string }).spec_json)).toEqual(JSON.parse(JSON.stringify(edited)));
  });

});
