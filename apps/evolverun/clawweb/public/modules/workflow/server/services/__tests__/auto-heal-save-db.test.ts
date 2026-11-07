// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import type { WorkflowSpec } from '../../workflow.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { mockPreparedSpecSave, preparedSaveIdentity } from './auto-heal-prepared-save-fixture.js';
import * as git from '../clawflows-git-client.js';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';

const spec: WorkflowSpec = { id: 'flow', version: '1', title: 'Original', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };
const config = { remoteUrl: 'https://invalid.example/repo', username: 'test', email: 'test@example.invalid', token: '' };

describe('AutoHeal DB transaction and policy within prepared save', () => {
  let raw: Database.Database, db: SqliteDatabase, specs: WorkflowSpecRepository, permissions: BotWorkflowPermissionRepository;
  let publication: ReturnType<typeof mockPreparedSpecSave>;
  const service = () => new AutoHealSaveService(db, specs, null, permissions, config, { name: 'Test', email: 'test@example.invalid' });
  const input = () => ({ workflowId: 'flow', actorId: 'owner', spec: { ...spec, title: 'Edited' } });
  const rows = () => raw.prepare('SELECT * FROM workflow_specs').all();
  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL UNIQUE, pack_id TEXT,
      spec_json TEXT NOT NULL, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT NOT NULL,
      workflow_id TEXT NOT NULL, env TEXT NOT NULL, can_view INTEGER, can_execute INTEGER, can_edit INTEGER,
      gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT);`);
    db = new SqliteDatabase(raw); specs = new WorkflowSpecRepository(db); permissions = new BotWorkflowPermissionRepository(db);
    publication = mockPreparedSpecSave(input => {
      expect(raw.inTransaction).toBe(false);
      expect(raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get(input.workflowId)).toEqual({ spec_json: JSON.stringify(input.spec) });
    });
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); });
  async function seed() {
    await specs.upsert('flow', 'pack', JSON.stringify(spec), 'owner');
    await permissions.upsert({ bot_id: null, bot_owner_id: 'owner', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 1 });
  }
  it('updates the DB draft before the prepared push and returns its exact identity', async () => {
    await seed();
    const clone = vi.spyOn(git, 'cloneClawflows');
    const runGit = vi.spyOn(gitExecution, 'executeGit');
    const operation = vi.spyOn(gitExecution, 'withGitOperation');
    const result = await service().save(input());
    expect(result).toEqual({ ok: true, saved: true, workflowId: 'flow', packId: 'pack', ...preparedSaveIdentity });
    expect(publication.push).toHaveBeenCalledOnce();
    const row = await specs.findByWorkflowId('flow');
    expect(row).toMatchObject({ pack_id: 'pack', title: 'Edited', owner_id: 'owner', version: null });
    expect(JSON.parse(row!.spec_json).title).toBe('Edited');
    expect(clone).not.toHaveBeenCalled();
    expect(runGit).not.toHaveBeenCalled(); expect(operation).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all()).toEqual([{ name: 'bot_workflow_permissions' }, { name: 'workflow_deploy_history' }, { name: 'workflow_specs' }]);
  });
  it('denies missing actors, unprivileged edits, absent workflow and duplicate create', async () => {
    await expect(service().save({ ...input(), actorId: '' })).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    await expect(service().save(input())).rejects.toMatchObject({ status: 404, code: 'WORKFLOW_NOT_FOUND' });
    await seed(); const before = rows();
    await expect(service().save({ ...input(), actorId: 'other' })).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    await expect(service().save({ ...input(), createOnly: true })).rejects.toMatchObject({ status: 409, code: 'WORKFLOW_ALREADY_EXISTS' });
    expect(rows()).toEqual(before);
  });
  it('rejects invalid specs, IDs, request identities and unsupported Git preconditions without writing', async () => {
    await seed(); const before = rows();
    for (const [change, code] of [
      [{ workflowId: '../flow' }, 'INVALID_REQUEST'],
      [{ spec: { ...spec, id: 'other' } }, 'WORKFLOW_ID_MISMATCH'],
      [{ spec: { ...spec, nodes: [] } }, 'INVALID_WORKFLOW'],
      [{ requestId: 'bad' }, 'INVALID_REQUEST'],
      [{ sourceCommit: 'a'.repeat(40) }, 'UNSUPPORTED_GIT_PRECONDITION'],
      [{ expectedSavedHead: null }, 'UNSUPPORTED_GIT_PRECONDITION'],
    ] as const) await expect(service().save({ ...input(), ...change })).rejects.toMatchObject({ status: 400, code });
    expect(rows()).toEqual(before);
  });
  it('propagates failed and ignored workflow writes', async () => {
    await seed(); const before = rows();
    raw.exec("CREATE TRIGGER fail_update BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'write failure'); END");
    await expect(service().save(input())).rejects.toThrow('write failure'); expect(rows()).toEqual(before);
    raw.exec('DROP TRIGGER fail_update; CREATE TRIGGER ignore_update BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END');
    await expect(service().save(input())).rejects.toThrow('Workflow update failed'); expect(rows()).toEqual(before);
    expect(publication.prepare).toHaveBeenCalledTimes(2); expect(publication.push).not.toHaveBeenCalled();
  });
  it.each(['wf', 'saved'])('rejects takeover of deleted DB workflow with real Git %s branch', async branch => {
    const dir = mkdtempSync(join(tmpdir(), 'auto-heal-ownership-'));
    try {
      const remote = join(dir, 'remote.git');
      const local = join(dir, 'local');
      const gitCommand = (...args: string[]) => execFileSync('git', args, { stdio: 'pipe' }).toString().trim();
      gitCommand('init', '--bare', remote);
      gitCommand('init', local);
      writeFileSync(join(local, 'workflow.yaml'), 'id: flow\n');
      gitCommand('-C', local, 'add', 'workflow.yaml');
      gitCommand('-C', local, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'existing workflow');
      gitCommand('-C', local, 'push', remote, `HEAD:refs/heads/${branch}/flow`);
      const refsBefore = gitCommand('--git-dir', remote, 'show-ref');
      await seed();
      raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('flow');
      const beforeSpecs = rows();
      const beforePermissions = raw.prepare('SELECT * FROM bot_workflow_permissions').all();
      const configured = new AutoHealSaveService(db, specs, null, permissions, { ...config, remoteUrl: remote });
      await expect(configured.save({ ...input(), actorId: 'attacker', createOnly: true }))
        .rejects.toMatchObject({ status: 503, code: 'CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED' });
      expect(rows()).toEqual(beforeSpecs);
      expect(raw.prepare('SELECT * FROM bot_workflow_permissions').all()).toEqual(beforePermissions);
      expect(await permissions.hasEditPermission('flow', 'attacker')).toBe(false);
      expect(gitCommand('--git-dir', remote, 'show-ref')).toBe(refsBefore);
    } finally { rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); }
  });
  it('fails closed for new IDs whether Git configuration is absent or unusable', async () => {
    const noGit = new AutoHealSaveService(db, specs, null, permissions);
    const beforeSpecs = rows();
    const beforePermissions = raw.prepare('SELECT * FROM bot_workflow_permissions').all();
    await expect(noGit.save({ ...input(), createOnly: true }))
      .rejects.toMatchObject({ status: 503, code: 'CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED' });
    vi.spyOn(git, 'initClawflowsReadRepo').mockRejectedValueOnce(new Error('Git unavailable'));
    await expect(service().save({ ...input(), createOnly: true })).rejects.toThrow('Git unavailable');
    expect(rows()).toEqual(beforeSpecs);
    expect(raw.prepare('SELECT * FROM bot_workflow_permissions').all()).toEqual(beforePermissions);
    expect(await permissions.hasEditPermission('flow', 'owner')).toBe(false);
  });
  it('does not overwrite a different row that replaced the authorized workflow', async () => {
    await seed();
    const permission = permissions.hasEditPermission.bind(permissions);
    vi.spyOn(permissions, 'hasEditPermission').mockImplementationOnce(async (...args) => {
      const allowed = await permission(...args);
      raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('flow');
      raw.prepare('INSERT INTO workflow_specs (id, workflow_id, spec_json, gmt_create, gmt_modified) VALUES (99, ?, ?, 0, 0)').run('flow', 'replacement');
      return allowed;
    });
    await expect(service().save(input())).rejects.toMatchObject({ status: 409, code: 'DRAFT_CONFLICT' });
    expect(raw.prepare('SELECT id, spec_json FROM workflow_specs').get()).toEqual({ id: 99, spec_json: 'replacement' });
  });
  it('preserves a user-authored content field without fabricating a projection', async () => {
    await seed();
    const draft = { ...input(), spec: { ...spec, title: 'Fresh', content: 'user payload' } };
    await service().save(draft);
    const stored = JSON.parse((await specs.findByWorkflowId('flow'))!.spec_json);
    expect(stored.title).toBe('Fresh');
    expect(stored.content).toBe('user payload');
  });

});
