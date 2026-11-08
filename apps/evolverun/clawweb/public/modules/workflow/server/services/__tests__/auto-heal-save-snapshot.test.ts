// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { parse, stringify } from 'yaml';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Request, Response, Router } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { createAutoHealRouter } from '../../routes/auto-heal.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { mockPreparedSpecSave, preparedSaveConfig, preparedSaveAuthor, preparedSaveIdentity } from './auto-heal-prepared-save-fixture.js';

const spec = { id: 'flow', version: '1', title: 'Original', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };
const input = { workflowId: 'flow', actorId: 'owner', spec: { ...spec, title: 'Repair' } };
const author = { name: 'Test', email: 'test@example.invalid' };
function post(router: Router, endpoint = 'save', body: unknown = input): Promise<{ status: number; body: Record<string, any> }> {
  return new Promise((resolve, reject) => {
    const req = { method: 'POST', url: `/${endpoint}`, body, headers: {}, get: () => undefined, header: () => undefined } as unknown as Request;
    let status = 200;
    const res = { status(code: number) { status = code; return this; }, json(body: Record<string, unknown>) { resolve({ status, body }); return this; } } as Response;
    router(req, res, (error?: unknown) => reject(error ?? new Error('No route response')));
  });
}

describe('AutoHeal existing draft transactional snapshot', () => {
  let dir: string, raw: Database.Database, writer: Database.Database, db: SqliteDatabase;
  let specs: WorkflowSpecRepository, permissions: BotWorkflowPermissionRepository;
  let gitMock: ReturnType<typeof mockPreparedSpecSave>;
  const service = () => new AutoHealSaveService(db, specs, null, permissions, preparedSaveConfig, preparedSaveAuthor);
  const row = () => writer.prepare('SELECT * FROM workflow_specs').all();
  beforeEach(async () => {
    dir = mkdtempSync(join(tmpdir(), 'auto-heal-snapshot-'));
    const file = join(dir, 'db.sqlite');
    raw = new Database(file); writer = new Database(file);
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT,
      title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT NOT NULL,
      workflow_id TEXT NOT NULL, env TEXT NOT NULL, can_view INTEGER, can_execute INTEGER, can_edit INTEGER,
      gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
      CREATE TABLE clawweb_pack_operations (request_id TEXT, workflow_id TEXT, phase TEXT, lock_workflow_id TEXT);
      CREATE TABLE cm_app_config (id INTEGER PRIMARY KEY, config_key TEXT UNIQUE, config_yaml TEXT, version INTEGER, enabled INTEGER, description TEXT, updated_by TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
    db = new SqliteDatabase(raw); specs = new WorkflowSpecRepository(db); permissions = new BotWorkflowPermissionRepository(db);
    await specs.upsert('flow', 'pack', JSON.stringify(spec), 'owner');
    await permissions.upsert({ bot_id: null, bot_owner_id: 'owner', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 1 });
    gitMock = mockPreparedSpecSave(candidate => {
      const committed = row()[0] as { spec_json: string; pack_id: string };
      expect(committed.pack_id).toBe(candidate.packId);
      expect(JSON.parse(committed.spec_json)).toEqual(candidate.spec);
    });
  });
  afterEach(async () => { vi.restoreAllMocks(); writer.close(); await db.close(); rmSync(dir, { recursive: true, force: true }); });

  // Hold the request after the preflight read but before BEGIN IMMEDIATE. The
  // independent SQLite connection commits an actual concurrent writer first.
  function pauseBeforeTransaction() {
    let reached!: () => void, resume!: () => void;
    const ready = new Promise<void>(r => { reached = r; });
    const gate = new Promise<void>(r => { resume = r; });
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(async fn => { reached(); await gate; return transaction(fn); });
    return { ready, resume };
  }
  it.each([
    ['document bytes', "UPDATE workflow_specs SET spec_json = '{ \"title\": \"winner\" }'"],
    ['pack', "UPDATE workflow_specs SET pack_id = 'winner-pack'"],
    ['title', "UPDATE workflow_specs SET title = 'winner'"],
    ['owner', "UPDATE workflow_specs SET owner_id = 'winner'"],
    ['version', 'UPDATE workflow_specs SET version = 42'],
    ['creation timestamp', 'UPDATE workflow_specs SET gmt_create = gmt_create + 1'],
    ['modification timestamp', 'UPDATE workflow_specs SET gmt_modified = gmt_modified + 1'],
    ['identity', "UPDATE workflow_specs SET id = 99"],
    ['rename', "UPDATE workflow_specs SET workflow_id = 'renamed'"],
    ['deletion', 'DELETE FROM workflow_specs'],
    ['replacement', "DELETE FROM workflow_specs; INSERT INTO workflow_specs VALUES (99, 'flow', 'pack', 'winner', 'winner', NULL, 'owner', 1, 1)"],
    ['unsafe bigint identity', 'UPDATE workflow_specs SET id = 9007199254740993'],
  ])('preserves committed concurrent %s change', async (_name, sql) => {
    const gate = pauseBeforeTransaction();
    const pending = service().save(input);
    await Promise.race([gate.ready, pending.then(() => { throw new Error('Save completed before transaction'); })]); writer.exec(sql); const winner = row(); gate.resume();
    await expect(pending).rejects.toMatchObject({ status: 409, code: 'DRAFT_CONFLICT' });
    expect(row()).toEqual(winner); expect(gitMock.push).not.toHaveBeenCalled();
  });
  it('distinguishes adjacent BIGINT identities that JavaScript cannot represent exactly', async () => {
    writer.exec('UPDATE workflow_specs SET id = 9007199254740992');
    const gate = pauseBeforeTransaction(); const pending = service().save(input);
    await Promise.race([gate.ready, pending.then(() => { throw new Error('Save completed before transaction'); })]); writer.exec('UPDATE workflow_specs SET id = 9007199254740993'); gate.resume();
    await expect(pending).rejects.toMatchObject({ status: 409, code: 'DRAFT_CONFLICT' });
    expect(gitMock.push).not.toHaveBeenCalled();
    expect(writer.prepare('SELECT CAST(id AS TEXT) AS id, title FROM workflow_specs').get())
      .toEqual({ id: '9007199254740993', title: 'Original' });
  });
  it('returns a sanitized conflict from the real save route', async () => {
    const gate = pauseBeforeTransaction();
    const router = createAutoHealRouter({ db, workflowSpecRepo: specs, deployHistoryRepo: new WorkflowDeployHistoryRepository(db),
      botPermRepo: permissions, resolvePrincipal: async () => ({ actorId: 'owner' }), gitConfig: preparedSaveConfig, gitAuthor: preparedSaveAuthor });
    const pending = post(router);
    await Promise.race([gate.ready, pending.then(() => { throw new Error('Save completed before transaction'); })]); writer.prepare('UPDATE workflow_specs SET spec_json = ?').run('private concurrent payload'); gate.resume();
    const response = await pending;
    expect(response).toEqual({ status: 409, body: { error: 'Save could not be completed', code: 'DRAFT_CONFLICT', requestId: undefined, branch: undefined, commit: undefined } });
    expect(JSON.stringify(response)).not.toContain('private concurrent payload');
    expect(gitMock.push).not.toHaveBeenCalled();
    expect((row()[0] as { spec_json: string }).spec_json).toBe('private concurrent payload');
  });
  it('allows only one of two AutoHeal requests that read the same baseline', async () => {
    let reached!: () => void, resume!: () => void, count = 0;
    const ready = new Promise<void>(resolve => { reached = resolve; });
    const gate = new Promise<void>(resolve => { resume = resolve; });
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementation(async fn => {
      if (++count === 2) reached();
      await gate;
      return transaction(fn);
    });
    const pending = Promise.allSettled(['First', 'Second'].map(title => service().save({ ...input, spec: { ...input.spec, title } })));
    await ready; resume();
    const result = await pending;
    expect(result.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    expect(result.filter(r => r.status === 'rejected')).toEqual([
      expect.objectContaining({ reason: expect.objectContaining({ status: 409, code: 'DRAFT_CONFLICT' }) }),
    ]);
    const winner = result.findIndex(r => r.status === 'fulfilled');
    expect((row()[0] as { title: string }).title).toBe(['First', 'Second'][winner]);
    expect(gitMock.push).toHaveBeenCalledTimes(1);
  });
  it('retains inherited Bot permissions while rechecking live membership', async () => {
    writer.exec("UPDATE bot_workflow_permissions SET bot_id = 'shared', bot_owner_id = 'bot-owner'");
    const listBots = vi.fn().mockResolvedValue([{ botId: 'shared', ownerId: 'bot-owner' }]);
    permissions = new BotWorkflowPermissionRepository(db, { listBots });
    const gate = pauseBeforeTransaction(); const pending = service().save(input);
    await Promise.race([gate.ready, pending.then(() => { throw new Error('Save completed before transaction'); })]); listBots.mockResolvedValue([]); gate.resume();
    await expect(pending).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });
    expect(listBots).toHaveBeenCalledTimes(2);
    expect(gitMock.push).not.toHaveBeenCalled();
    expect((row()[0] as { title: string }).title).toBe('Original');
  });
  it('saves the full Pack to wf, then deploys that exact repair to Git and history', async () => {
    gitMock.prepare.mockRestore();
    vi.stubEnv('GIT_ALLOW_PROTOCOL', 'file');
    vi.stubEnv('GIT_CONFIG_NOSYSTEM', '1'); vi.stubEnv('GIT_CONFIG_GLOBAL', '/dev/null');
    const git = (cwd: string, ...args: string[]) => execFileSync('git', args, {
      cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    }).trim();
    try {
      const remote = join(dir, 'remote.git'), seed = join(dir, 'seed');
      git(dir, 'init', '--bare', '--initial-branch=main', remote);
      git(dir, 'init', '--initial-branch=main', seed);
      git(seed, 'config', 'user.name', author.name); git(seed, 'config', 'user.email', author.email);
      mkdirSync(join(seed, 'workflows')); mkdirSync(join(seed, 'scripts'));
      writeFileSync(join(seed, 'workflow.pack.yaml'), stringify({ id: 'pack', version: '1.0.0', workflows: [{ id: 'flow', file: 'workflows/flow.yaml' }] }));
      writeFileSync(join(seed, 'workflows/flow.yaml'), stringify(spec)); writeFileSync(join(seed, 'scripts/run.sh'), 'untouched\n');
      git(seed, 'add', '.'); git(seed, 'commit', '-m', 'initial');
      git(seed, 'push', remote, 'HEAD:refs/heads/main', 'HEAD:refs/heads/wf/flow');
      const remoteGit = (...args: string[]) => git(dir, '--git-dir', remote, ...args);
      const originalCommit = remoteGit('rev-parse', 'refs/heads/wf/flow');
      raw.exec(`CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
        CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);`);
      const router = createAutoHealRouter({ db, workflowSpecRepo: specs, deployHistoryRepo: new WorkflowDeployHistoryRepository(db),
        botPermRepo: permissions, resolvePrincipal: async () => ({ actorId: 'owner' }),
        gitConfig: { remoteUrl: remote, username: author.name, email: author.email }, gitAuthor: author });
      const saved = await post(router);
      expect(saved).toMatchObject({ status: 200, body: { ok: true, saved: true, workflowId: 'flow', packId: 'pack', branch: 'wf/flow' } });
      expect(saved.body.commit).toMatch(/^[0-9a-f]{40}$/);
      expect(saved.body.commit).not.toBe(originalCommit);
      expect(JSON.parse((row()[0] as { spec_json: string }).spec_json)).toEqual(input.spec);
      expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved.body.commit);
      expect(writer.prepare('SELECT * FROM workflow_deploy_history').all()).toEqual([]);
      const result = await post(router, 'deploy', { workflowId: 'flow', packId: 'pack', requestId: 'snapshot-regression-01', autoRun: false });
      expect(result).toMatchObject({ status: 200, body: { ok: true, deployed: true, workflowId: 'flow', packId: 'pack', deployNumber: 1 } });
      expect(parse(remoteGit('show', `${result.body.commit}:workflows/flow.yaml`))).toEqual(input.spec);
      expect(remoteGit('show', `${result.body.commit}:scripts/run.sh`)).toBe('untouched');
      expect(result.body.commit).toBe(saved.body.commit);
      expect(remoteGit('rev-parse', 'refs/tags/deploy/flow/#1^{}')).toBe(saved.body.commit);
      expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(saved.body.commit);
      expect(writer.prepare('SELECT deploy_number, is_active FROM workflow_deploy_history').all()).toEqual([{ deploy_number: 1, is_active: 1 }]);
    } finally { vi.unstubAllEnvs(); }
  }, 30_000);
  it('rechecks revocation before revealing a conflict', async () => {
    const gate = pauseBeforeTransaction(); const pending = service().save(input);
    await Promise.race([gate.ready, pending.then(() => { throw new Error('Save completed before transaction'); })]); writer.exec("UPDATE workflow_specs SET title = 'winner'; UPDATE bot_workflow_permissions SET can_edit = 0");
    const winner = row(); gate.resume();
    await expect(pending).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' }); expect(row()).toEqual(winner); expect(gitMock.push).not.toHaveBeenCalled();
  });
  it('rechecks the legacy guard inside the transaction', async () => {
    const gate = pauseBeforeTransaction(); const pending = service().save(input);
    await Promise.race([gate.ready, pending.then(() => { throw new Error('Save completed before transaction'); })]); writer.exec("INSERT INTO clawweb_pack_operations VALUES ('request', 'flow', 'unknown', 'flow')");
    const winner = row(); gate.resume();
    await expect(pending).rejects.toMatchObject({ status: 409, code: 'LEGACY_PACK_OPERATION_UNRESOLVED' }); expect(row()).toEqual(winner); expect(gitMock.push).not.toHaveBeenCalled();
  });
  it('rolls back a trigger that changes persisted fields after update', async () => {
    const before = row();
    writer.exec("CREATE TRIGGER corrupt AFTER UPDATE ON workflow_specs BEGIN UPDATE workflow_specs SET title = 'wrong' WHERE id = NEW.id; END");
    await expect(service().save(input)).rejects.toThrow('Workflow update failed'); expect(row()).toEqual(before); expect(gitMock.push).not.toHaveBeenCalled();
  });
  it.each(['mysql', 'zdas'] as const)('uses draft then permission locking before snapshot reads on %s', async dbType => {
    const statements: string[] = [];
    const wrapped: IDatabase = { dbType, dialect: db.dialect, query: db.query.bind(db), exec: db.exec.bind(db), close: async () => {},
      transaction: fn => db.transaction(tx => fn({ ...wrapped, query: <T>(sql: string, params?: unknown[]) => {
        statements.push(sql); return tx.query<T>(sql.replace(/ FOR UPDATE$/, ''), params);
      }, exec: tx.exec.bind(tx) })) };
    const svc = new AutoHealSaveService(wrapped, specs, null, new BotWorkflowPermissionRepository(wrapped), preparedSaveConfig, preparedSaveAuthor);
    expect(await svc.save(input)).toMatchObject({ saved: true, ...preparedSaveIdentity });
    expect(gitMock.push).toHaveBeenCalledTimes(1);
    expect(statements[0]).toMatch(/FROM workflow_specs WHERE workflow_id = \? FOR UPDATE$/);
    expect(statements[1]).toMatch(/FROM bot_workflow_permissions WHERE workflow_id = \? ORDER BY id FOR UPDATE$/);
    expect(statements.slice(2).some(sql => sql.includes('clawweb_pack_operations'))).toBe(true);
  });
});
