// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { mockPreparedSpecSave, preparedSaveConfig, preparedSaveAuthor } from './auto-heal-prepared-save-fixture.js';
import { editorActiveDeploymentToken } from '../editor-active-deployment-token.js';

const spec = { id: 'flow', version: '1', title: 'Repair', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };

describe('active deployment token with a concurrent SQLite writer', () => {
  let dir: string, raw: Database.Database, writer: Database.Database, db: SqliteDatabase;
  let history: WorkflowDeployHistoryRepository, service: AutoHealSaveService;
  let gitMock: ReturnType<typeof mockPreparedSpecSave>;
  beforeEach(async () => {
    dir = mkdtempSync(join(tmpdir(), 'auto-heal-active-token-'));
    const file = join(dir, 'db.sqlite'); raw = new Database(file); writer = new Database(file);
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT,
      title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT, deploy_number INTEGER,
      version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT,
      owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT,
      env TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER);`);
    db = new SqliteDatabase(raw); history = new WorkflowDeployHistoryRepository(db);
    const permissions = new BotWorkflowPermissionRepository(db);
    await history.insert({ workflowId: 'flow', packId: 'pack', deployNumber: 7, version: 2, action: 'deploy',
      tagName: 'deploy/flow/#7', specJson: JSON.stringify(spec), isActive: true });
    await permissions.upsert({ bot_id: null, bot_owner_id: 'editor', workflow_id: 'flow', can_view: 1, can_execute: 0, can_edit: 1 });
    gitMock = mockPreparedSpecSave(candidate => {
      const committed = writer.prepare('SELECT pack_id, spec_json FROM workflow_specs').get() as { pack_id: string; spec_json: string };
      expect(committed.pack_id).toBe(candidate.packId);
      expect(JSON.parse(committed.spec_json)).toEqual(candidate.spec);
    });
    service = new AutoHealSaveService(db, new WorkflowSpecRepository(db), history, permissions, preparedSaveConfig, preparedSaveAuthor);
  });
  afterEach(async () => { vi.restoreAllMocks(); writer.close(); await db.close(); rmSync(dir, { recursive: true, force: true }); });

  it.each([
    ['spec_json', "UPDATE workflow_deploy_history SET spec_json = 'concurrent private change' WHERE is_active = 1"],
    ['pack_id', "UPDATE workflow_deploy_history SET pack_id = 'concurrent-pack' WHERE is_active = 1"],
    ['ambiguous text', "UPDATE workflow_deploy_history SET spec_json = 'concurrent \uFFFD' WHERE is_active = 1"],
  ])('rejects a same-number %s mutation committed between reads', async (_field, sql) => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    let reached!: () => void, resume!: () => void;
    const ready = new Promise<void>(resolve => { reached = resolve; });
    const gate = new Promise<void>(resolve => { resume = resolve; });
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(async fn => { reached(); await gate; return transaction(fn); });
    const pending = service.save({ workflowId: 'flow', actorId: 'editor', spec, packId: active.pack_id,
      expectedDraftAbsent: true, expectedActiveDeployNumber: 7, expectedActiveToken: editorActiveDeploymentToken('flow', active) });
    await Promise.race([ready, pending.then(() => { throw new Error('Save completed before transaction'); })]);
    writer.exec(sql);
    const winner = writer.prepare('SELECT * FROM workflow_deploy_history').all();
    resume();
    await expect(pending).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(gitMock.push).not.toHaveBeenCalled();
    expect(writer.prepare('SELECT * FROM workflow_specs').all()).toEqual([]);
    expect(writer.prepare('SELECT * FROM workflow_deploy_history').all()).toEqual(winner);
    expect((winner[0] as { deploy_number: number }).deploy_number).toBe(7);
  });
});
