// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { DirectoryBot } from '@avernet/clawweb-shared/server/services/bot-directory';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import type { WorkflowSpec } from '../../workflow.js';
import { AutoHealSaveService } from '../auto-heal-save-service.js';
import { editorActiveDeploymentToken, InvalidEditorActiveDeploymentTokenInputError } from '../editor-active-deployment-token.js';
import * as activeTokenHelper from '../editor-active-deployment-token.js';
import * as git from '../clawflows-git-client.js';
import * as packSpec from '../pack-spec-save.js';
import { mockPreparedSpecSave, preparedSaveIdentity, preparedSaveConfig, preparedSaveAuthor } from './auto-heal-prepared-save-fixture.js';

const published: WorkflowSpec = { id: 'flow', version: '7', title: 'Published', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };
const applied = { ...published, title: 'Applied in memory', content: 'user-authored content' };
const input = () => ({ workflowId: 'flow', actorId: 'editor', spec: applied });
const notFound = { status: 404, code: 'WORKFLOW_NOT_FOUND' };
const forbidden = { status: 403, code: 'FORBIDDEN' };
const conflict = { status: 409, code: 'WORKFLOW_ALREADY_EXISTS' };

describe('AutoHeal first-draft DB and policy checks inside prepared publication', () => {
  let raw: Database.Database, db: SqliteDatabase;
  let specs: WorkflowSpecRepository, history: WorkflowDeployHistoryRepository, permissions: BotWorkflowPermissionRepository;
  const service = () => new AutoHealSaveService(db, specs, history, permissions, preparedSaveConfig, preparedSaveAuthor);
  const drafts = () => raw.prepare('SELECT * FROM workflow_specs ORDER BY id').all();
  // Compare every persisted value, including the original spec_json bytes and timestamps.
  const historyBytes = () => JSON.stringify(raw.prepare('SELECT * FROM workflow_deploy_history ORDER BY id').all());
  const permissionBytes = () => JSON.stringify(raw.prepare('SELECT * FROM bot_workflow_permissions ORDER BY id').all());
  beforeEach(async () => {
    raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL UNIQUE, pack_id TEXT,
      spec_json TEXT NOT NULL, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
      CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, pack_id TEXT NOT NULL,
      deploy_number INTEGER NOT NULL, version INTEGER NOT NULL, tag_name TEXT, action TEXT NOT NULL,
      from_deploy_number INTEGER, spec_json TEXT NOT NULL, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER NOT NULL,
      gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT NOT NULL,
      workflow_id TEXT NOT NULL, env TEXT NOT NULL, can_view INTEGER, can_execute INTEGER, can_edit INTEGER,
      gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL);`);
    db = new SqliteDatabase(raw); specs = new WorkflowSpecRepository(db);
    history = new WorkflowDeployHistoryRepository(db); permissions = new BotWorkflowPermissionRepository(db);
    await history.insert({ workflowId: 'flow', packId: 'published-pack', deployNumber: 9, version: 7,
      action: 'deploy', tagName: 'deploy/flow/#9', specJson: JSON.stringify(published, null, 2),
      note: 'Keep this publication', ownerId: 'publisher', botId: 'published-bot', isActive: true });
    await history.insert({ workflowId: 'flow', packId: 'older-pack', deployNumber: 8, version: 6,
      action: 'deploy', tagName: 'deploy/flow/#8', specJson: '{ "title": "older" }', ownerId: 'publisher' });
    await permissions.upsert({ bot_id: null, bot_owner_id: 'editor', workflow_id: 'flow', can_view: 1, can_execute: 0, can_edit: 1 });
    // Isolate transport, not the real DB transaction, active token or permission policy.
    vi.spyOn(git, 'createTempRepo').mockRejectedValue(new Error('Git unavailable'));
    vi.spyOn(git, 'cloneClawflows').mockRejectedValue(new Error('Git unavailable'));
    vi.spyOn(gitExecution, 'executeGit').mockRejectedValue(new Error('Git unavailable'));
    vi.spyOn(gitExecution, 'withGitOperation').mockRejectedValue(new Error('Git unavailable'));
    mockPreparedSpecSave(input => {
      expect(raw.inTransaction).toBe(false);
      expect(raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get(input.workflowId)).toEqual({ spec_json: JSON.stringify(input.spec) });
    });
  });
  afterEach(async () => {
    try {
      for (const spy of [git.createTempRepo, git.cloneClawflows, gitExecution.executeGit,
        gitExecution.withGitOperation]) expect(spy).not.toHaveBeenCalled();
    } finally { vi.restoreAllMocks(); await db.close(); }
  });
  function beforeTransaction(change: () => void) {
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(async fn => { change(); return transaction(fn); });
  }
  function journal() {
    raw.exec(`CREATE TABLE clawweb_pack_operations (request_id TEXT, workflow_id TEXT, phase TEXT, lock_workflow_id TEXT);
      INSERT INTO clawweb_pack_operations VALUES ('legacy-save', 'flow', 'unknown', 'flow');`);
  }

  async function editorInput() {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    return { ...input(), packId: active.pack_id, expectedDraftAbsent: true as const,
      expectedActiveDeployNumber: active.deploy_number, expectedActiveToken: editorActiveDeploymentToken('flow', active) };
  }

  it('creates the explicit Editor first draft without preparing or pushing Git', async () => {
    const beforeHistory = historyBytes(), beforePermissions = permissionBytes();
    expect(await service().saveEditorFirstDraft(await editorInput())).toEqual({ ok: true, saved: true, workflowId: 'flow', packId: 'published-pack' });
    expect(await specs.findByWorkflowId('flow')).toMatchObject({ spec_json: JSON.stringify(applied), owner_id: 'editor', pack_id: 'published-pack' });
    expect(packSpec.withPreparedWorkflowSpec).not.toHaveBeenCalled();
    expect(historyBytes()).toBe(beforeHistory); expect(permissionBytes()).toBe(beforePermissions);
  });

  it.each(['revoked edit', 'active changed', 'competing draft'] as const)('rejects %s under the first-draft transaction without Git', async mode => {
    const request = await editorInput();
    beforeTransaction(() => {
      if (mode === 'revoked edit') raw.exec('UPDATE bot_workflow_permissions SET can_edit = 0');
      if (mode === 'active changed') raw.exec("UPDATE workflow_deploy_history SET spec_json = 'changed' WHERE is_active = 1");
      if (mode === 'competing draft') raw.prepare('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?)')
        .run('flow', 'published-pack', '{"winner":true}', 'winner', 'other', 1, 1);
    });
    await expect(service().saveEditorFirstDraft(request)).rejects.toMatchObject({ status: mode === 'revoked edit' ? 403 : 409,
      code: mode === 'revoked edit' ? 'FORBIDDEN' : mode === 'active changed' ? 'BASELINE_STALE' : 'DRAFT_ALREADY_EXISTS' });
    expect(packSpec.withPreparedWorkflowSpec).not.toHaveBeenCalled();
    expect(drafts()).toHaveLength(mode === 'competing draft' ? 1 : 0);
    if (mode === 'competing draft') expect((await specs.findByWorkflowId('flow'))?.spec_json).toBe('{"winner":true}');
  });

  it('requires complete explicit Editor preconditions and forbids create-only', async () => {
    const request = await editorInput();
    for (const change of [{ expectedDraftAbsent: undefined }, { expectedActiveDeployNumber: undefined },
      { expectedActiveToken: undefined }, { createOnly: true }]) {
      await expect(service().saveEditorFirstDraft({ ...request, ...change } as never)).rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    }
    expect(drafts()).toEqual([]); expect(packSpec.withPreparedWorkflowSpec).not.toHaveBeenCalled();
  });

  it.each([undefined, null, 'published-pack'])('inserts the applied document with DB pack identity (packId=%s)', async packId => {
    const beforeHistory = historyBytes(), beforePermissions = permissionBytes();
    const lookup = vi.spyOn(history, 'findActiveByWorkflowId');
    expect(await service().save({ ...input(), packId })).toEqual({ ok: true, saved: true, workflowId: 'flow', packId: 'published-pack', ...preparedSaveIdentity });
    expect(lookup).toHaveBeenCalledWith('flow');
    expect(drafts()).toHaveLength(1);
    expect(await specs.findByWorkflowId('flow')).toMatchObject({ pack_id: 'published-pack', owner_id: 'editor',
      title: applied.title, spec_json: JSON.stringify(applied), version: null });
    expect(historyBytes()).toBe(beforeHistory); expect(permissionBytes()).toBe(beforePermissions);
    expect(await history.getLatestVersion('flow')).toBe(7);
    expect(await history.getMaxDeployNumber('published-pack', 'flow')).toBe(9);
  });

  it.each(['inactive', 'absent', 'different-workflow'])('rejects %s history even when edit permission exists', async state => {
    if (state === 'inactive') raw.exec('UPDATE workflow_deploy_history SET is_active = 0');
    if (state === 'absent') raw.exec('DELETE FROM workflow_deploy_history');
    if (state === 'different-workflow') raw.exec("UPDATE workflow_deploy_history SET workflow_id = 'other'");
    const before = historyBytes();
    await expect(service().save(input())).rejects.toMatchObject(notFound);
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it('does not permit an arbitrary new workflow ID', async () => {
    await expect(service().save({ ...input(), workflowId: 'new-flow', spec: { ...applied, id: 'new-flow' } })).rejects.toMatchObject(notFound);
    expect(drafts()).toEqual([]);
  });

  it('fails closed with a null history repository and a legacy minimal schema', async () => {
    raw.exec('DROP TABLE workflow_deploy_history; CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT)');
    const query = vi.spyOn(db, 'query');
    await expect(new AutoHealSaveService(db, specs, null, permissions).save(input())).rejects.toMatchObject(notFound);
    expect(query.mock.calls.some(([sql]) => sql.includes('workflow_deploy_history'))).toBe(false);
    expect(drafts()).toEqual([]);
  });

  it.each(['', 'intruder', 'publisher'])('requires edit permission rather than history ownership for actor %s', async actorId => {
    const beforeHistory = historyBytes(), beforePermissions = permissionBytes();
    await expect(service().save({ ...input(), actorId })).rejects.toMatchObject(forbidden);
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(beforeHistory); expect(permissionBytes()).toBe(beforePermissions);
  });

  it('rejects missing permission repositories and execute-only grants', async () => {
    await expect(new AutoHealSaveService(db, specs, history, null).save(input())).rejects.toMatchObject(forbidden);
    raw.exec('UPDATE bot_workflow_permissions SET can_edit = 0, can_execute = 1');
    await expect(service().save(input())).rejects.toMatchObject(forbidden);
    expect(drafts()).toEqual([]);
  });

  it('honors the existing workflow-wide wildcard edit permission contract', async () => {
    raw.exec("UPDATE bot_workflow_permissions SET bot_owner_id = '*', bot_id = '*'");
    expect(await service().save(input())).toMatchObject({ saved: true });
    expect((await specs.findByWorkflowId('flow'))?.owner_id).toBe('editor');
  });

  it('rejects client-side pack renames instead of taking over a different pack', async () => {
    await expect(service().save({ ...input(), packId: 'attacker-pack' })).rejects.toMatchObject({ status: 409, code: 'PACK_ID_MISMATCH' });
    expect(drafts()).toEqual([]);
  });

  it('does not invent a pack identity for an invalid active row', async () => {
    raw.exec("UPDATE workflow_deploy_history SET pack_id = '' WHERE is_active = 1");
    await expect(service().save(input())).rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    expect(drafts()).toEqual([]);
  });

  it('preserves workflow validation and Git-precondition rejection before restoring a draft', async () => {
    for (const [change, code] of [
      [{ workflowId: '../flow' }, 'INVALID_REQUEST'],
      [{ spec: { ...published, id: 'other' } }, 'WORKFLOW_ID_MISMATCH'],
      [{ spec: { ...published, nodes: [] } }, 'INVALID_WORKFLOW'],
      [{ requestId: 'bad' }, 'INVALID_REQUEST'],
      [{ sourceCommit: 'a'.repeat(40) }, 'UNSUPPORTED_GIT_PRECONDITION'],
      [{ expectedSavedHead: null }, 'UNSUPPORTED_GIT_PRECONDITION'],
    ] as const) await expect(service().save({ ...input(), ...change })).rejects.toMatchObject({ status: 400, code });
    expect(drafts()).toEqual([]);
  });

  it.each(['preflight', 'transaction'])('keeps the legacy operation guard at %s', async stage => {
    if (stage === 'preflight') journal(); else beforeTransaction(journal);
    const before = historyBytes();
    await expect(service().save(input())).rejects.toMatchObject({ status: 409, code: 'LEGACY_PACK_OPERATION_UNRESOLVED' });
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it.each([undefined, true] as const)('does not replace a concurrent draft (expectedDraftAbsent=%s)', async expectedDraftAbsent => {
    let winner: unknown[] = [];
    const authorize = permissions.hasEditPermission.bind(permissions);
    vi.spyOn(permissions, 'hasEditPermission').mockImplementationOnce(async (...args) => {
      const allowed = await authorize(...args);
      await specs.upsert('flow', 'published-pack', JSON.stringify({ ...published, title: 'Concurrent winner' }), 'other-editor');
      winner = drafts(); return allowed;
    });
    await expect(service().save({ ...input(), expectedDraftAbsent, expectedActiveDeployNumber: expectedDraftAbsent ? 9 : undefined })).rejects.toMatchObject(
      expectedDraftAbsent ? { status: 409, code: 'DRAFT_ALREADY_EXISTS' } : conflict);
    expect(winner).toHaveLength(1); expect(drafts()).toEqual(winner);
  });

  it.each([undefined, true] as const)('maps a real SQLite unique conflict to 409 (expectedDraftAbsent=%s)', async expectedDraftAbsent => {
    raw.exec(`CREATE TRIGGER competing_insert BEFORE INSERT ON workflow_specs BEGIN
      INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, owner_id, gmt_create, gmt_modified)
      VALUES (NEW.workflow_id, 'published-pack', '{}', 'competing', 'other-editor', 0, 0); END;`);
    const before = historyBytes();
    await expect(service().save({ ...input(), expectedDraftAbsent, expectedActiveDeployNumber: expectedDraftAbsent ? 9 : undefined })).rejects.toMatchObject(
      expectedDraftAbsent ? { status: 409, code: 'DRAFT_ALREADY_EXISTS' } : conflict);
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it.each(['ABORT', 'IGNORE'])('rolls back failed or zero-row inserts (%s)', async mode => {
    raw.exec(`CREATE TABLE insert_effects (value TEXT);
      CREATE TRIGGER fail_insert BEFORE INSERT ON workflow_specs BEGIN
      INSERT INTO insert_effects VALUES ('must roll back');
      SELECT RAISE(${mode}${mode === 'ABORT' ? ", 'insert failed'" : ''}); END;`);
    const beforeHistory = historyBytes(), beforePermissions = permissionBytes();
    await expect(service().save(input())).rejects.toThrow(mode === 'ABORT' ? 'insert failed' : 'Workflow creation failed');
    expect(drafts()).toEqual([]); expect(raw.prepare('SELECT * FROM insert_effects').all()).toEqual([]);
    expect(raw.inTransaction).toBe(false);
    expect(historyBytes()).toBe(beforeHistory); expect(permissionBytes()).toBe(beforePermissions);
  });

  it('rechecks edit permission inside the transaction after revocation', async () => {
    beforeTransaction(() => raw.exec('UPDATE bot_workflow_permissions SET can_edit = 0'));
    await expect(service().save(input())).rejects.toMatchObject(forbidden);
    expect(drafts()).toEqual([]);
    expect(await permissions.hasEditPermission('flow', 'editor')).toBe(false);
  });

  it('rechecks active existence inside the transaction after deactivation', async () => {
    beforeTransaction(() => raw.exec('UPDATE workflow_deploy_history SET is_active = 0'));
    await expect(service().save(input())).rejects.toMatchObject(notFound);
    expect(drafts()).toEqual([]);
  });

  it.each([
    "pack_id = 'replacement-pack'", "owner_id = 'replacement-owner'", 'deploy_number = 10, version = 8',
    "spec_json = '{\"title\":\"replacement\"}'",
  ])('does not trust stale active context: %s', async assignment => {
    beforeTransaction(() => raw.exec(`UPDATE workflow_deploy_history SET ${assignment} WHERE is_active = 1`));
    await expect(service().save(input())).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]);
  });

  it('propagates history lookup failures without making a draft', async () => {
    const failure = new Error('History unavailable');
    vi.spyOn(history, 'findActiveByWorkflowId').mockRejectedValueOnce(failure);
    await expect(service().save(input())).rejects.toBe(failure);
    expect(drafts()).toEqual([]);
  });

  it('updates an existing draft through prepared publication without consulting history', async () => {
    await specs.upsert('flow', 'draft-pack', JSON.stringify(published), 'editor');
    await specs.updateVersion('flow', 7);
    const beforeHistory = historyBytes();
    const lookup = vi.spyOn(history, 'findActiveByWorkflowId').mockRejectedValue(new Error('History unavailable'));
    const configured = new AutoHealSaveService(db, specs, history, permissions,
      { remoteUrl: 'https://invalid.example/repo', username: 'test', email: 'test@example.invalid', token: '' },
      { name: 'Test', email: 'test@example.invalid' });
    expect(await configured.save(input())).toEqual({ ok: true, saved: true, workflowId: 'flow', packId: 'draft-pack', ...preparedSaveIdentity });
    expect(await specs.findByWorkflowId('flow')).toMatchObject({ spec_json: JSON.stringify(applied), version: 7 });
    expect(lookup).not.toHaveBeenCalled(); expect(historyBytes()).toBe(beforeHistory);
  });

  it('accepts expectedDraftAbsent only when there really is no draft', async () => {
    const before = historyBytes();
    expect(await service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })).toMatchObject({ saved: true, packId: 'published-pack' });
    expect(historyBytes()).toBe(before);
    const winner = drafts();
    await expect(service().save({ ...input(), spec: { ...applied, title: 'Late request' }, expectedDraftAbsent: true, expectedActiveDeployNumber: 9 }))
      .rejects.toMatchObject({ status: 409, code: 'DRAFT_ALREADY_EXISTS' });
    expect(drafts()).toEqual(winner);
  });

  it.each([false, null, 'true', 0, 1, {}, []])('rejects an invalid expectedDraftAbsent value: %j', async expectedDraftAbsent => {
    await expect(service().save({ ...input(), expectedDraftAbsent } as never)).rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    expect(drafts()).toEqual([]);
  });

  it.each([true, false])('rejects combining expectedDraftAbsent with createOnly=%s', async createOnly => {
    await expect(service().save({ ...input(), expectedDraftAbsent: true, createOnly })).rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    expect(drafts()).toEqual([]);
  });
  it.each([null, '', 'a'.repeat(63), 'g'.repeat(64), 42, {}, []])('rejects invalid active token %j', async expectedActiveToken => {
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken } as never))
      .rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    expect(drafts()).toEqual([]);
  });
  it('requires the draft-absence and deploy-number preconditions for an active token', async () => {
    for (const options of [{ expectedActiveToken: 'a'.repeat(64) },
      { expectedDraftAbsent: true, expectedActiveToken: 'a'.repeat(64) },
      { expectedActiveDeployNumber: 9, expectedActiveToken: 'a'.repeat(64) }]) {
      await expect(service().save({ ...input(), ...options } as never)).rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    }
    expect(drafts()).toEqual([]);
  });

  it('requires an explicit active-deployment number when expectedDraftAbsent is used', async () => {
    await expect(service().save({ ...input(), expectedDraftAbsent: true })).rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    expect(drafts()).toEqual([]);
  });

  it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, '9', null])('rejects an invalid expected active deploy number: %j', async expectedActiveDeployNumber => {
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber } as never))
      .rejects.toMatchObject({ status: 400, code: 'INVALID_REQUEST' });
    expect(drafts()).toEqual([]);
  });

  it('accepts an exact active token and preserves the unchanged legacy payload', async () => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active).toUpperCase();
    expect(await service().save({ ...input(), packId: active.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 9, expectedActiveToken })).toMatchObject({ saved: true, packId: active.pack_id });
    raw.exec('DELETE FROM workflow_specs');
    expect(await service().save(input())).toMatchObject({ saved: true, packId: active.pack_id });
  });

  it.each([
    ['spec_json', "UPDATE workflow_deploy_history SET spec_json = '{ \"title\": \"changed\" }' WHERE is_active = 1"],
    ['pack_id', "UPDATE workflow_deploy_history SET pack_id = 'changed-pack' WHERE is_active = 1"],
  ])('rejects stale %s when the deployment number is unchanged at the initial read', async (_field, sql) => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    raw.exec(sql); const before = historyBytes();
    await expect(service().save({ ...input(), packId: active.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 9, expectedActiveToken })).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it.each(['first read', 'locked read'])('rejects U+FFFD in the %s active row without writing', async stage => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    const mutate = () => raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run('published \uFFFD text');
    if (stage === 'first read') mutate(); else beforeTransaction(mutate);
    await expect(service().save({ ...input(), packId: active.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 9, expectedActiveToken })).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]);
    expect((await history.findActiveByWorkflowId('flow'))?.spec_json).toBe('published \uFFFD text');
  });

  it('maps invalid token integer input to a conflict without changing legacy behavior', async () => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    raw.exec('UPDATE workflow_deploy_history SET version = -1 WHERE is_active = 1');
    const before = historyBytes();
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken }))
      .rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })).resolves.toMatchObject({ saved: true });
    expect(historyBytes()).toBe(before);
  });

  it('propagates a database read error when a token precondition is present', async () => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const failure = new Error('private database failure');
    vi.spyOn(history, 'findActiveByWorkflowId').mockRejectedValueOnce(failure);
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9,
      expectedActiveToken: editorActiveDeploymentToken('flow', active) })).rejects.toBe(failure);
    expect(drafts()).toEqual([]);
  });

  it.each(['first read', 'locked read'])('maps the dedicated token error independent of its message at %s', async stage => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const activeHash = editorActiveDeploymentToken('flow', active);
    const original = activeTokenHelper.editorActiveDeploymentToken;
    const compute = vi.spyOn(activeTokenHelper, 'editorActiveDeploymentToken');
    if (stage === 'locked read') compute.mockImplementationOnce(original);
    compute.mockImplementationOnce(() => { throw new InvalidEditorActiveDeploymentTokenInputError('a new diagnostic message'); });
    const before = historyBytes();
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken: activeHash }))
      .rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it.each([new Error('crypto unavailable'), new Error('Invalid active workflow token row'),
    new Error('Invalid active workflow token integer'), new Error('Ambiguous active workflow token text'),
    new TypeError('Ambiguous active workflow token text')])
    ('does not translate unrelated token computation failures: %s', async failure => {
      const active = (await history.findActiveByWorkflowId('flow'))!;
      const activeHash = editorActiveDeploymentToken('flow', active);
      vi.spyOn(activeTokenHelper, 'editorActiveDeploymentToken').mockImplementationOnce(() => { throw failure; });
      await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken: activeHash }))
        .rejects.toBe(failure);
      expect(drafts()).toEqual([]);
    });

  it('propagates a locked database read failure with the token precondition', async () => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    const activeHash = editorActiveDeploymentToken('flow', active);
    const failure = new Error('private database lock failure');
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(fn => transaction(tx => {
      const failing: IDatabase = { dbType: tx.dbType, dialect: tx.dialect,
        query: <T>(sql: string, params?: unknown[]) => sql.includes('FROM workflow_deploy_history')
          ? Promise.reject(failure) : tx.query<T>(sql, params),
        exec: tx.exec.bind(tx), transaction: tx.transaction.bind(tx), close: tx.close.bind(tx) };
      return fn(failing);
    }));
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken: activeHash }))
      .rejects.toBe(failure);
    expect(drafts()).toEqual([]);
  });

  it('keeps legacy number-only behavior when an active spec changed before the request', async () => {
    raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run('earlier change');
    expect(await service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 }))
      .toMatchObject({ saved: true, packId: 'published-pack' });
    expect(drafts()).toHaveLength(1);
  });

  it('still checks request pack identity even with a valid active token', async () => {
    const active = (await history.findActiveByWorkflowId('flow'))!;
    await expect(service().save({ ...input(), packId: 'other-pack', expectedDraftAbsent: true,
      expectedActiveDeployNumber: 9, expectedActiveToken: editorActiveDeploymentToken('flow', active) }))
      .rejects.toMatchObject({ status: 409, code: 'PACK_ID_MISMATCH' });
    expect(drafts()).toEqual([]);
  });

  it('computes the token from the locked current row, not the earlier object', async () => {
    const real = history.findActiveByWorkflowId.bind(history);
    const initial = (await real('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', initial);
    const updatedJson = '{ "title": "concurrent" }';
    // Make the old JSON.stringify snapshot comparison pass despite the changed
    // DB row. Only hashing the actual locked current row detects the mismatch.
    let captured: NonNullable<typeof initial>;
    vi.spyOn(history, 'findActiveByWorkflowId').mockImplementationOnce(async id => {
      const row = (await real(id))!;
      Object.setPrototypeOf(row, { toJSON() { return { ...this, spec_json: updatedJson }; } });
      captured = row;
      return row;
    });
    beforeTransaction(() => {
      raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run(updatedJson);
      const current = raw.prepare(`SELECT pack_id, deploy_number, version, tag_name, action, spec_json, bot_id,
        owner_id, gmt_create, gmt_modified FROM workflow_deploy_history WHERE is_active = 1`).get();
      expect(JSON.stringify(captured)).toBe(JSON.stringify(current));
    });
    await expect(service().save({ ...input(), packId: initial.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 9, expectedActiveToken })).rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]);
    expect((await real('flow'))?.spec_json).toBe(updatedJson);
  });

  it('rejects an old active snapshot before inserting its workflow document', async () => {
    raw.exec('UPDATE workflow_deploy_history SET deploy_number = 10, version = 10 WHERE is_active = 1');
    const before = historyBytes();
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 }))
      .rejects.toMatchObject({ status: 409, code: 'BASELINE_STALE' });
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  function inheritedPermission() {
    raw.exec("UPDATE bot_workflow_permissions SET bot_id = 'shared-bot', bot_owner_id = 'bot-owner'");
    const bots: DirectoryBot[] = [{ botId: 'shared-bot', displayBotId: 'shared-bot', ownerId: 'bot-owner',
      status: 'active', source: 'test', accessType: 'collaborator' }];
    const listBots = vi.fn().mockResolvedValue(bots);
    permissions = new BotWorkflowPermissionRepository(db, { listBots });
    return listBots;
  }

  it('retains injected Bot-sharing edit policy when binding permissions to the transaction', async () => {
    const listBots = inheritedPermission();
    expect(await permissions.hasEditPermission('flow', 'editor')).toBe(true);
    listBots.mockClear();
    const beforeHistory = historyBytes(), beforePermissions = permissionBytes();
    expect(await service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })).toMatchObject({ saved: true });
    expect(listBots).toHaveBeenCalledTimes(3); // preflight, transaction and post-commit/pre-push
    expect(listBots).toHaveBeenNthCalledWith(1, 'editor', 'all');
    expect(listBots).toHaveBeenNthCalledWith(2, 'editor', 'all');
    expect(listBots).toHaveBeenNthCalledWith(3, 'editor', 'all');
    expect((await specs.findByWorkflowId('flow'))?.owner_id).toBe('editor');
    expect(historyBytes()).toBe(beforeHistory); expect(permissionBytes()).toBe(beforePermissions);
  });

  it.each(['membership', 'grant', 'directory-failure'])('rechecks inherited edit rights after %s revocation', async kind => {
    const listBots = inheritedPermission();
    beforeTransaction(() => {
      if (kind === 'membership') listBots.mockResolvedValue([]);
      if (kind === 'grant') raw.exec('UPDATE bot_workflow_permissions SET can_edit = 0');
      if (kind === 'directory-failure') listBots.mockRejectedValue(new Error('Directory unavailable'));
    });
    const before = historyBytes();
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })).rejects.toMatchObject(forbidden);
    expect(listBots).toHaveBeenCalledTimes(2); // rejected in the transaction; no post-commit push check
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it.each(['mysql', 'zdas'] as const)('locks active before the first draft read on %s (SQL-order contract, not engine emulation)', async dbType => {
    raw.exec('CREATE TABLE clawweb_pack_operations (request_id TEXT, workflow_id TEXT, phase TEXT, lock_workflow_id TEXT)');
    const statements: string[] = [];
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(fn => transaction(tx => {
      const recording: IDatabase = { dbType, dialect: tx.dialect,
        query: <T>(sql: string, params?: unknown[]) => {
          statements.push(sql);
          return tx.query<T>(sql.replace(/ FOR UPDATE$/, ''), params);
        },
        exec: tx.exec.bind(tx), transaction: tx.transaction.bind(tx), close: tx.close.bind(tx),
      };
      return fn(recording);
    }));
    expect(await service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })).toMatchObject({ saved: true });
    expect(statements[0]).toMatch(/FROM workflow_deploy_history[\s\S]*FOR UPDATE$/);
    expect(statements[1]).toMatch(/^SELECT id FROM bot_workflow_permissions[\s\S]*FOR UPDATE$/);
    expect(statements[2]).toMatch(/^SELECT id FROM workflow_specs WHERE workflow_id = \?$/);
    // This is the first snapshot read, after active and grant locks. Do not lock a
    // missing draft gap or invert the existing publication draft -> active locks.
    expect(statements[2]).not.toContain('FOR UPDATE');
  });

  it('allows one of two concurrent first saves and preserves the winner', async () => {
    const outcomes = await Promise.allSettled(['First', 'Second'].map(title => service().save({ ...input(),
      expectedDraftAbsent: true, expectedActiveDeployNumber: 9, spec: { ...applied, title } })));
    expect(outcomes.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(outcomes.filter(result => result.status === 'rejected')).toEqual([
      expect.objectContaining({ reason: expect.objectContaining({ status: 409, code: 'DRAFT_ALREADY_EXISTS' }) }),
    ]);
    expect(drafts()).toHaveLength(1);
    const winner = outcomes.findIndex(result => result.status === 'fulfilled');
    expect((await specs.findByWorkflowId('flow'))?.title).toBe(['First', 'Second'][winner]);
  });

  it('does not misclassify a database deadlock as a concurrent-draft conflict', async () => {
    const deadlock = Object.assign(new Error('Deadlock found'), { code: 'ER_LOCK_DEADLOCK' });
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(fn => transaction(tx => {
      vi.spyOn(tx, 'exec').mockRejectedValueOnce(deadlock);
      return fn(tx);
    }));
    await expect(service().save({ ...input(), expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })).rejects.toBe(deadlock);
    expect(drafts()).toEqual([]);
  });

  it('does not change the explicit createOnly Git protocol', async () => {
    await expect(new AutoHealSaveService(db, specs, history, permissions).save({ ...input(), createOnly: true })).rejects.toMatchObject({
      status: 503, code: 'CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED',
    });
    const configured = new AutoHealSaveService(db, specs, history, permissions,
      { remoteUrl: 'https://invalid.example/repo', username: 'test', email: 'test@example.invalid', token: '' },
      { name: 'Test', email: 'test@example.invalid' });
    await expect(configured.save({ ...input(), createOnly: true })).rejects.toMatchObject(conflict);
    expect(drafts()).toEqual([]);
  });
});
