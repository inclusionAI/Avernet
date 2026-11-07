// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { mockPreparedSpecSave, preparedSaveAuthor, preparedSaveConfig, preparedSaveIdentity } from './auto-heal-prepared-save-fixture.js';

const spec = { id: 'flow', title: 'repaired', nodes: [{ id: 'n', executor: { type: 'agent' } }] };

describe('legacy AutoHeal save Pack binding compatibility', () => {
  let raw: Database.Database;
  let db: SqliteDatabase;
  let service: AutoHealSaveService;
  let publication: ReturnType<typeof mockPreparedSpecSave>;
  const row = () => raw.prepare('SELECT pack_id, spec_json FROM workflow_specs').get();
  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0);`);
    db = new SqliteDatabase(raw);
    publication = mockPreparedSpecSave(input => {
      expect(input.workflowId).toBe('flow');
      expect(input.spec).toEqual(spec);
      expect(raw.inTransaction).toBe(false);
      expect(row()).toEqual({ pack_id: input.packId, spec_json: JSON.stringify(input.spec) });
    });
    service = new AutoHealSaveService(db, new WorkflowSpecRepository(db), null, new BotWorkflowPermissionRepository(db), preparedSaveConfig, preparedSaveAuthor);
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); });
  function seed(packId: string | null) {
    raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', packId, JSON.stringify({ ...spec, title: 'before' }), 'before', 1, 'editor', 1, 2);
  }
  it.each([
    { stored: 'old-pack', supplied: 'new-pack', effective: 'new-pack' },
    { stored: null, supplied: 'new-pack', effective: 'new-pack' },
    { stored: 'old-pack', supplied: 'old-pack', effective: 'old-pack' },
    { stored: 'old-pack', supplied: null, effective: 'old-pack' },
    { stored: null, supplied: null, effective: 'flow' },
    { stored: 'old-pack', supplied: undefined, effective: 'old-pack' },
    { stored: null, supplied: undefined, effective: 'flow' },
  ])('saves stored=$stored supplied=$supplied as $effective', async ({ stored, supplied, effective }) => {
    seed(stored);
    const input = { workflowId: 'flow', actorId: 'editor', spec };
    const request = supplied === undefined ? input : { ...input, packId: supplied };
    await expect(service.save(request)).resolves.toMatchObject({ saved: true, packId: effective, ...preparedSaveIdentity });
    expect(row()).toMatchObject({ pack_id: effective, spec_json: JSON.stringify(spec) });
    expect(publication.prepare).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'flow', packId: effective, spec }), expect.any(Function));
    expect(publication.push).toHaveBeenCalledOnce();
    expect(await publication.push.mock.results[0]!.value).toMatchObject(preparedSaveIdentity);
  });
  it('rechecks revoked edit permission in the transaction before rebinding', async () => {
    seed('old-pack');
    const before = raw.prepare('SELECT * FROM workflow_specs').get();
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(async fn => {
      raw.exec('UPDATE bot_workflow_permissions SET can_edit = 0');
      return transaction(fn);
    });
    await expect(service.save({ workflowId: 'flow', actorId: 'editor', spec, packId: 'new-pack' }))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    expect(raw.prepare('SELECT * FROM workflow_specs').get()).toEqual(before);
    expect(publication.push).not.toHaveBeenCalled();
  });
  it.each(['old-pack', null])('denies an unauthorized rebind without changing stored=%s', async stored => {
    seed(stored);
    const before = raw.prepare('SELECT * FROM workflow_specs').get();
    await expect(service.save({ workflowId: 'flow', actorId: 'intruder', spec, packId: 'new-pack' }))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    expect(raw.prepare('SELECT * FROM workflow_specs').get()).toEqual(before);
    expect(publication.push).not.toHaveBeenCalled();
  });
});
