// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { EditorDraftCasError, EditorDraftCasService } from '../editor-draft-cas-service.js';

const spec = { id: 'flow', version: '1.0.0', title: 'Original', content: 'user content', nodes: [
  { id: 'start', type: 'start', title: 'Start', config: {}, executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', title: 'End', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
] };

describe('existing editor draft CAS', () => {
  let raw: Database.Database;
  let db: SqliteDatabase;
  let service: EditorDraftCasService;
  const read = () => service.read('flow', 'editor');
  const save = (token: string, changes: Record<string, unknown> = {}) => service.save({
    workflowId: 'flow', actorId: 'editor', expectedDraftToken: token, spec: { ...spec, title: 'Edited' }, ...changes,
  });
  const row = () => raw.prepare('SELECT * FROM workflow_specs').get();
  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT UNIQUE NOT NULL COLLATE NOCASE, pack_id TEXT, spec_json TEXT NOT NULL, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0), (2,NULL,'viewer','flow',1,0,0,'test',0,0), (3,NULL,'edit-only','flow',0,0,1,'test',0,0);`);
    raw.prepare('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,version,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?,?)')
      .run('flow', 'pack', JSON.stringify(spec, null, 2), 'Original', 1, 'editor', 1, 2);
    db = new SqliteDatabase(raw);
    service = new EditorDraftCasService(db, new BotWorkflowPermissionRepository(db));
  });
  afterEach(async () => { await db.close(); });

  it('returns the exact stored bytes and saves the submitted document, including content', async () => {
    const before = await read();
    expect(before).toEqual({ workflowId: 'flow', packId: 'pack', specJson: JSON.stringify(spec, null, 2), draftToken: expect.stringMatching(/^[a-f0-9]{64}$/) });
    const submitted = { ...spec, title: 'Edited', content: 'not a projection' };
    const result = await save(before.draftToken, { spec: submitted });
    expect(result).toMatchObject({ workflowId: 'flow', packId: 'pack', specJson: JSON.stringify(submitted) });
    expect(result.draftToken).not.toBe(before.draftToken);
    expect(await read()).toEqual(result);
  });

  it('returns legacy content wrappers verbatim and treats content as a real submitted field', async () => {
    const legacy = JSON.stringify({ content: 'id: flow\nnodes: []' });
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(legacy);
    const before = await read();
    expect(before.specJson).toBe(legacy);
    const submitted = { ...spec, content: 'new literal content' };
    const after = await save(before.draftToken, { spec: submitted });
    expect(JSON.parse(after.specJson).content).toBe('new literal content');
  });

  it.each([2, null, undefined])('persists the submitted legacy version %s without normalizing it', async version => {
    const before = await read();
    const submitted: Record<string, unknown> = { ...spec, title: 'Edited', version };
    if (version === undefined) delete submitted.version;
    const result = await save(before.draftToken, { spec: submitted });
    expect(JSON.parse(result.specJson)).toEqual(submitted);
    expect(JSON.parse((row() as { spec_json: string }).spec_json)).toEqual(submitted);
  });

  it('allows exact no-op without writing and preserves token and metadata', async () => {
    raw.prepare('UPDATE workflow_specs SET spec_json = ?').run(JSON.stringify(spec));
    const before = await read(), previous = row();
    raw.exec("CREATE TRIGGER no_updates BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'must not write'); END");
    expect(await save(before.draftToken, { spec, packId: 'pack' })).toEqual(before);
    expect(row()).toEqual(previous);
  });

  it('rejects stale tokens without any writes, including after deletion and recreation', async () => {
    const before = await read();
    await save(before.draftToken);
    const changed = row();
    await expect(save(before.draftToken)).rejects.toMatchObject({ status: 409, code: 'DRAFT_CONFLICT' });
    expect(row()).toEqual(changed);
    raw.exec('DELETE FROM workflow_specs');
    raw.prepare('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,version,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?,?)')
      .run('flow', 'pack', before.specJson, 'Original', 1, 'editor', 1, 2);
    const recreated = row();
    await expect(save(before.draftToken)).rejects.toMatchObject({ status: 409, code: 'DRAFT_CONFLICT' });
    expect(row()).toEqual(recreated);
  });

  it('rolls back SQL failures and cannot treat an ignored update as success', async () => {
    const before = await read(), previous = row();
    raw.exec("CREATE TRIGGER deny_update BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(ABORT, 'private detail'); END");
    await expect(save(before.draftToken)).rejects.toBeInstanceOf(EditorDraftCasError);
    expect(row()).toEqual(previous);
    raw.exec('DROP TRIGGER deny_update; CREATE TRIGGER ignore_update BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END');
    await expect(save(before.draftToken)).rejects.toBeInstanceOf(EditorDraftCasError);
    expect(row()).toEqual(previous);
    raw.exec("DROP TRIGGER ignore_update; CREATE TRIGGER corrupt_update AFTER UPDATE ON workflow_specs BEGIN UPDATE workflow_specs SET spec_json = 'corrupted'; END");
    await expect(save(before.draftToken)).rejects.toMatchObject({ status: 500 });
    expect(row()).toEqual(previous);
  });

  it('enforces explicit identities and view or edit for read, edit for save', async () => {
    raw.exec("INSERT INTO bot_workflow_permissions VALUES (4,NULL,'*','flow',1,0,1,'test',0,0)");
    const before = await read();
    expect(await service.read('flow', 'viewer')).toEqual(before);
    expect(await service.read('flow', 'edit-only')).toEqual(before);
    for (const actorId of ['', ' ', '*', 'unknown', null]) {
      await expect(service.read('flow', actorId as string)).rejects.toMatchObject({ status: 403 });
      await expect(save(before.draftToken, { actorId })).rejects.toMatchObject({ status: 403 });
    }
    raw.exec("DELETE FROM bot_workflow_permissions WHERE id = 4");
    await expect(service.read('flow', 'stranger')).rejects.toMatchObject({ status: 403 });
    await expect(save(before.draftToken, { actorId: 'viewer' })).rejects.toMatchObject({ status: 403 });
    expect((row() as { spec_json: string }).spec_json).toBe(before.specJson);
  });

  it('requires exact workflow identity even with a case-insensitive database collation', async () => {
    const token = (await read()).draftToken;
    await expect(service.read('FLOW', 'editor')).rejects.toBeInstanceOf(EditorDraftCasError);
    await expect(save(token, { workflowId: 'FLOW', spec: { ...spec, id: 'FLOW' } })).rejects.toBeInstanceOf(EditorDraftCasError);
    await expect(save(token, { spec: { ...spec, id: ' flow ' } })).rejects.toMatchObject({ status: 400 });
  });

  it('validates token, spec, and pack binding without leaking contents', async () => {
    const token = (await read()).draftToken, previous = row();
    for (const invalid of ['', 'A'.repeat(64), 'a'.repeat(63), null]) {
      await expect(save(invalid as string)).rejects.toMatchObject({ status: 400 });
    }
    for (const invalid of [null, 7, {}, { ...spec, nodes: [] }, { ...spec, id: 'other' }]) {
      await expect(save(token, { spec: invalid })).rejects.toMatchObject({ status: 400 });
    }
    await expect(save(token, { packId: undefined })).rejects.toMatchObject({ status: 400 });
    for (const packId of [null, 2, '', 'another']) {
      await expect(save(token, { packId })).rejects.toBeInstanceOf(EditorDraftCasError);
    }
    expect(row()).toEqual(previous);
  });

  it('fails closed on unknown database types and zero-row changed writes', async () => {
    const before = await read(), previous = row();
    const unavailable = Object.create(db) as SqliteDatabase;
    Object.defineProperty(unavailable, 'dbType', { value: 'noop' });
    const noDb = new EditorDraftCasService(unavailable, new BotWorkflowPermissionRepository(db));
    await expect(noDb.save({ workflowId: 'flow', actorId: 'editor', expectedDraftToken: before.draftToken, spec })).rejects.toMatchObject({ status: 503 });
    const transaction = db.transaction.bind(db);
    db.transaction = fn => transaction(tx => fn(new Proxy(tx, { get(target, key, receiver) {
      if (key === 'exec') return async (sql: string, params: unknown[]) => { await target.exec(sql, params); return { affectedRows: 0 }; };
      return Reflect.get(target, key, receiver);
    } })));
    await expect(save(before.draftToken)).rejects.toMatchObject({ status: 500, code: 'DRAFT_STORAGE_ERROR' });
    expect(row()).toEqual(previous);
  });

  it('blocks writes while a legacy Pack operation is unresolved without changing the draft', async () => {
    raw.exec(`CREATE TABLE clawweb_pack_operations (request_id TEXT, workflow_id TEXT, phase TEXT, lock_workflow_id TEXT);
      INSERT INTO clawweb_pack_operations VALUES ('old-request', 'flow', 'pushing', 'flow')`);
    const before = await read(), previous = row();
    await expect(save(before.draftToken)).rejects.toMatchObject({ status: 409, code: 'LEGACY_PACK_OPERATION_UNRESOLVED' });
    expect(row()).toEqual(previous);
    raw.exec("UPDATE clawweb_pack_operations SET phase = 'completed', lock_workflow_id = NULL");
    expect((await save(before.draftToken)).specJson).toContain('Edited');
  });

  it('fails closed when legacy-operation retirement cannot be verified', async () => {
    raw.exec('CREATE TABLE clawweb_pack_operations (wrong_column TEXT)');
    const before = await read(), previous = row();
    await expect(save(before.draftToken)).rejects.toMatchObject({ status: 500, code: 'DRAFT_STORAGE_ERROR' });
    expect(row()).toEqual(previous);
  });

  it('preserves a null pack and rejects missing drafts', async () => {
    raw.exec('UPDATE workflow_specs SET pack_id = NULL');
    const before = await read();
    expect((await save(before.draftToken)).packId).toBeNull();
    const current = await read();
    raw.exec('DELETE FROM workflow_specs');
    await expect(save(current.draftToken)).rejects.toMatchObject({ status: 409 });
    await expect(read()).rejects.toMatchObject({ status: 404 });
  });
});

// Separate processes prevent synchronous SQLite busy waits from blocking the winner.
it('serializes two real SQLite connections using the same token', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'draft-cas-'));
  const filename = join(dir, 'draft.sqlite');
  const raw = new Database(filename);
  raw.exec(`PRAGMA journal_mode=WAL;
    CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT UNIQUE NOT NULL, pack_id TEXT, spec_json TEXT NOT NULL, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0);`);
  raw.prepare('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,version,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?,?)')
    .run('flow', 'pack', JSON.stringify(spec), 'Original', 1, 'editor', 1, 2);
  const db = new SqliteDatabase(raw);
  const children: ReturnType<typeof fork>[] = [];
  try {
    const token = (await new EditorDraftCasService(db, new BotWorkflowPermissionRepository(db)).read('flow', 'editor')).draftToken;
    const runner = fileURLToPath(new URL('../../../../../../node_modules/vite-node/vite-node.mjs', import.meta.url));
    const fixture = fileURLToPath(new URL('./editor-draft-cas-worker.ts', import.meta.url));
    children.push(...['One', 'Two'].map(title => fork(runner, ['--config', 'vitest.config.ts', fixture, filename, token, title], { stdio: ['ignore', 'pipe', 'pipe', 'ipc'] })));
    const outcomes = children.map(child => new Promise<{ status: string; code?: string }>((resolve, reject) => {
      let stderr = ''; child.stderr!.on('data', chunk => { stderr += chunk; });
      child.on('error', reject);
      child.on('exit', code => { if (code !== 0) reject(new Error(`worker failed ${code}: ${stderr}`)); });
      child.on('message', message => {
        const value = message as { status: string; code?: string };
        if (value.status === 'ready') { if (children.every(c => (c as typeof c & { ready?: boolean }).ready)) children.forEach(c => c.send('go'));
          else { (child as typeof child & { ready?: boolean }).ready = true; if (children.every(c => (c as typeof c & { ready?: boolean }).ready)) children.forEach(c => c.send('go')); }
        } else resolve(value);
      });
    }));
    const results = await Promise.all(outcomes);
    expect(results.filter(result => result.status === 'ok')).toHaveLength(1);
    expect(results.filter(result => result.status === 'error' && result.code === 'DRAFT_CONFLICT')).toHaveLength(1);
    expect(['One', 'Two']).toContain(JSON.parse((await db.query<{ spec_json: string }>('SELECT spec_json FROM workflow_specs'))[0].spec_json).title);
  } finally { children.forEach(child => { if (child.exitCode === null) child.kill(); }); await db.close(); rmSync(dir, { recursive: true, force: true }); }
});
