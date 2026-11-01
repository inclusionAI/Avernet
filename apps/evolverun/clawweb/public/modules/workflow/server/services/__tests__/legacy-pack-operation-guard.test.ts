// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { AutoHealDeployService } from '../auto-heal-deploy-service.js';
import { assertNoUnresolvedLegacyPackOperation } from '../legacy-pack-operation-guard.js';
import * as git from '../clawflows-git-client.js';

const spec = { id: 'flow', version: '1', title: 'Original', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };
const gitConfig = { remoteUrl: 'https://invalid.example/repo', username: 'user', email: 'user@example.invalid', token: '' };
const author = { name: 'User', email: 'user@example.invalid' };
const blocked = { status: 409, code: 'LEGACY_PACK_OPERATION_UNRESOLVED', message: expect.stringContaining('reconcile') };

describe('legacy pack journal retirement guard', () => {
  let raw: Database.Database;
  let db: SqliteDatabase;
  beforeEach(() => { raw = new Database(':memory:'); db = new SqliteDatabase(raw); });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); });
  function journal() { raw.exec('CREATE TABLE clawweb_pack_operations (request_id TEXT, workflow_id TEXT, phase TEXT, lock_workflow_id INTEGER)'); }
  function add(workflow: string, phase: string, lock: number | null = null) {
    raw.prepare('INSERT INTO clawweb_pack_operations VALUES (?, ?, ?, ?)').run(`${workflow}-${phase}`, workflow, phase, lock);
  }
  it('allows an absent SQLite journal without creating it', async () => {
    await expect(assertNoUnresolvedLegacyPackOperation(db, 'flow')).resolves.toBeUndefined();
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE name = 'clawweb_pack_operations'").all()).toEqual([]);
  });
  it.each(['preparing', 'prepared', 'pushing', 'unknown', 'unexpected'])('blocks unresolved %s for its workflow only', async phase => {
    journal(); add('other', phase); await assertNoUnresolvedLegacyPackOperation(db, 'flow');
    add('flow', phase); await expect(assertNoUnresolvedLegacyPackOperation(db, 'flow')).rejects.toMatchObject(blocked);
  });
  it('allows terminal unlocked rows but blocks inconsistent locks', async () => {
    journal(); add('flow', 'completed'); add('flow', 'failed');
    await assertNoUnresolvedLegacyPackOperation(db, 'flow'); add('flow', 'failed', 1);
    await expect(assertNoUnresolvedLegacyPackOperation(db, 'flow')).rejects.toMatchObject(blocked);
  });
  it('propagates incompatible schemas and other SQLite errors', async () => {
    raw.exec('CREATE TABLE clawweb_pack_operations (request_id TEXT)');
    await expect(assertNoUnresolvedLegacyPackOperation(db, 'flow')).rejects.toThrow('no such column');
    const failure = Object.assign(new Error('no such table: clawweb_pack_operations'), { code: 'SQLITE_AUTH' });
    vi.spyOn(db, 'query').mockRejectedValueOnce(failure);
    await expect(assertNoUnresolvedLegacyPackOperation(db, 'flow')).rejects.toBe(failure);
  });
  it.each(['mysql', 'zdas'] as const)('%s tolerates only confirmed missing journal errors', async dbType => {
    const query = vi.fn().mockRejectedValue({ code: 'ER_NO_SUCH_TABLE', errno: 1146, sqlMessage: "Table 'test.clawweb_pack_operations' doesn't exist" });
    const managed = { dbType, query } as unknown as IDatabase;
    await expect(assertNoUnresolvedLegacyPackOperation(managed, 'flow')).resolves.toBeUndefined();
    expect(query).toHaveBeenCalledWith(expect.stringContaining('clawweb_pack_operations'), ['flow']);
    for (const failure of [
      { code: 'ER_TABLEACCESS_DENIED_ERROR', message: 'permission denied' },
      { code: 'ER_NO_SUCH_TABLE', errno: 1146, sqlMessage: "Table 'test.other_table' doesn't exist" },
      // The server's 1146 text is not normalized by mysql2. Unknown ZDAS
      // formatting must fail closed until it can be identified safely.
      { code: 'ER_NO_SUCH_TABLE', errno: 1146, sqlMessage: 'unrecognized missing table format' },
      { code: 'ECONNRESET', message: 'connection reset' },
    ]) {
      query.mockRejectedValueOnce(failure);
      await expect(assertNoUnresolvedLegacyPackOperation(managed, 'flow')).rejects.toBe(failure);
    }
    query.mockResolvedValueOnce([{ request_id: 'old' }]);
    await expect(assertNoUnresolvedLegacyPackOperation(managed, 'flow')).rejects.toMatchObject(blocked);
  });
  describe('service preflight', () => {
    let specs: WorkflowSpecRepository, permissions: BotWorkflowPermissionRepository;
    beforeEach(async () => {
      raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
        CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT);
        CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER);`);
      specs = new WorkflowSpecRepository(db); permissions = new BotWorkflowPermissionRepository(db);
      await specs.upsert('flow', 'pack', JSON.stringify(spec), 'user');
      await permissions.upsert({ bot_id: null, bot_owner_id: 'user', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 1 });
    });
    const saveInput = { workflowId: 'flow', actorId: 'user', spec: { ...spec, title: 'Changed' } };
    function save() { return new AutoHealSaveService(db, specs, null, permissions, gitConfig, author); }
    function deploy() { return new AutoHealDeployService(db, specs, new WorkflowDeployHistoryRepository(db), permissions, gitConfig, author); }
    it('blocks save and deploy before database writes or Git work', async () => {
      journal(); add('flow', 'unknown');
      const original = raw.prepare('SELECT * FROM workflow_specs').all();
      const temp = vi.spyOn(git, 'createTempRepo');
      await expect(save().save(saveInput)).rejects.toMatchObject(blocked);
      await expect(deploy().deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toMatchObject(blocked);
      expect(raw.prepare('SELECT * FROM workflow_specs').all()).toEqual(original);
      expect(raw.prepare('SELECT * FROM workflow_deploy_history').all()).toEqual([]);
      expect(temp).not.toHaveBeenCalled();
    });
    it('blocks create-only for a deleted workflow before Git or writes', async () => {
      journal(); add('new-flow', 'pushing'); const temp = vi.spyOn(git, 'createTempRepo');
      await expect(save().save({ ...saveInput, workflowId: 'new-flow', spec: { ...spec, id: 'new-flow' }, createOnly: true })).rejects.toMatchObject(blocked);
      expect(temp).not.toHaveBeenCalled();
      expect(raw.prepare('SELECT workflow_id FROM workflow_specs').all()).toEqual([{ workflow_id: 'flow' }]);
    });
    it('propagates a journal query failure before either service has side effects', async () => {
      const original = db.query.bind(db);
      const failure = Object.assign(new Error('permission denied'), { code: 'SQLITE_AUTH' });
      vi.spyOn(db, 'query').mockImplementation((sql, params) => sql.includes('clawweb_pack_operations') ? Promise.reject(failure) : original(sql, params));
      const temp = vi.spyOn(git, 'createTempRepo');
      await expect(save().save(saveInput)).rejects.toBe(failure);
      await expect(deploy().deploy({ workflowId: 'flow', actorId: 'user' })).rejects.toBe(failure);
      expect(temp).not.toHaveBeenCalled(); expect((await specs.findByWorkflowId('flow'))?.title).toBe('Original');
    });
  });
});
