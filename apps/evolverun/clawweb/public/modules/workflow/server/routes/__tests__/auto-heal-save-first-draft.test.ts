// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import type { Request, Response } from 'express';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import * as git from '../../services/clawflows-git-client.js';
import { mockPreparedSpecSave, preparedSaveIdentity, preparedSaveConfig, preparedSaveAuthor } from '../../services/__tests__/auto-heal-prepared-save-fixture.js';
import * as gitSettings from '../../config/auto-heal-git-config.js';
import { createAutoHealRouter, type AutoHealRouteDependencies } from '../auto-heal.js';
import { editorActiveDeploymentToken } from '../../services/editor-active-deployment-token.js';
import { AutoHealSaveService } from '../../services/auto-heal-save-service.js';

const spec = { id: 'flow', title: 'Applied draft', version: '3', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };

describe('POST /api/auto-heal/save first-draft policy with prepared Git transport', () => {
  let raw: Database.Database, db: SqliteDatabase, deps: AutoHealRouteDependencies;
  const drafts = () => raw.prepare('SELECT * FROM workflow_specs').all();
  const historyBytes = () => JSON.stringify(raw.prepare('SELECT * FROM workflow_deploy_history ORDER BY id').all());
  function post(body: unknown = { workflowId: 'flow', spec }, endpoint = '/save'): Promise<{ status: number; body: Record<string, unknown> }> {
    return new Promise((resolve, reject) => {
      const request = { method: 'POST', url: endpoint, body, headers: {} } as Request;
      let status = 200;
      const response = { status(code: number) { status = code; return this; },
        json(payload: Record<string, unknown>) { resolve({ status, body: payload }); return this; } } as Response;
      createAutoHealRouter(deps)(request, response, error => reject(error ?? new Error('Route did not respond')));
    });
  }
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
    db = new SqliteDatabase(raw);
    const workflowSpecRepo = new WorkflowSpecRepository(db), deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
    const botPermRepo = new BotWorkflowPermissionRepository(db);
    deps = { db, workflowSpecRepo, deployHistoryRepo, botPermRepo, resolvePrincipal: async () => ({ actorId: 'editor' }),
      gitConfig: { remoteUrl: '', username: '', email: '', token: '' }, gitAuthor: { name: '', email: '' }, triggerRun: vi.fn() };
    await deployHistoryRepo.insert({ workflowId: 'flow', packId: 'active-pack', version: 3, deployNumber: 5,
      tagName: 'deploy/flow/#5', action: 'deploy', specJson: JSON.stringify({ ...spec, title: 'Published' }, null, 2),
      ownerId: 'publisher', isActive: true });
    await botPermRepo.upsert({ workflow_id: 'flow', bot_id: null, bot_owner_id: 'editor', can_view: 1, can_execute: 0, can_edit: 1 });
    const query = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation((sql, params) => {
      if (sql.includes('cm_app_config')) throw new Error('Git config unavailable');
      return query(sql, params);
    });
    vi.spyOn(gitSettings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig: preparedSaveConfig, gitAuthor: preparedSaveAuthor });
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
      for (const spy of [git.createTempRepo, git.cloneClawflows,
        gitExecution.executeGit, gitExecution.withGitOperation, deps.triggerRun]) {
        expect(spy).not.toHaveBeenCalled();
      }
      expect(vi.mocked(db.query).mock.calls.some(([sql]) => sql.includes('cm_app_config'))).toBe(false);
    } finally { vi.restoreAllMocks(); await db.close(); }
  });

  const editorEndpoint = '/editor-save-first-draft';
  const validPreconditions = { expectedDraftAbsent: true, expectedActiveDeployNumber: 5, expectedActiveToken: 'a'.repeat(64) };

  it.each([
    ['expectedDraftAbsent', undefined], ['expectedDraftAbsent', false], ['expectedDraftAbsent', 'true'],
    ['expectedActiveDeployNumber', undefined], ['expectedActiveDeployNumber', null], ['expectedActiveDeployNumber', 0],
    ['expectedActiveDeployNumber', -1], ['expectedActiveDeployNumber', 1.5], ['expectedActiveDeployNumber', Number.MAX_SAFE_INTEGER + 1],
    ['expectedActiveDeployNumber', '5'], ['expectedActiveToken', undefined], ['expectedActiveToken', null],
    ['expectedActiveToken', ''], ['expectedActiveToken', 'a'.repeat(63)], ['expectedActiveToken', 'z'.repeat(64)],
    ['expectedActiveToken', 'A'.repeat(64)],
    ['expectedActiveToken', {}], ['createOnly', true], ['createOnly', false],
  ])('rejects invalid or missing %s=%j before invoking the save service', async (field, value) => {
    const save = vi.spyOn(AutoHealSaveService.prototype, 'saveEditorFirstDraft');
    const before = historyBytes();
    expect(await post({ workflowId: 'flow', spec, ...validPreconditions, [field as string]: value }, editorEndpoint))
      .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(save).not.toHaveBeenCalled(); expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it('requires a host-verified identity, never trusting an actor in the request body', async () => {
    const save = vi.spyOn(AutoHealSaveService.prototype, 'saveEditorFirstDraft');
    for (const actor of [undefined, null, { actorId: 'unknown' }, { actorId: '  ' }]) {
      deps.resolvePrincipal = actor === undefined ? undefined : async () => actor;
      expect(await post({ workflowId: 'flow', spec, ...validPreconditions, actorId: 'editor' }, editorEndpoint))
        .toMatchObject({ status: 401 });
    }
    expect(save).not.toHaveBeenCalled(); expect(drafts()).toEqual([]);
  });

  it('requires workflow edit authorization without persisting a draft', async () => {
    deps.resolvePrincipal = async () => ({ actorId: 'intruder' });
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    expect(await post({ workflowId: 'flow', spec, ...validPreconditions,
      expectedActiveToken: editorActiveDeploymentToken('flow', active) }, editorEndpoint))
      .toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(drafts()).toEqual([]);
  });

  it('passes all preconditions and trusted actor to the service and saves the matching first draft', async () => {
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    const save = vi.spyOn(AutoHealSaveService.prototype, 'saveEditorFirstDraft');
    const before = historyBytes();
    expect(await post({ workflowId: 'flow', spec, packId: active.pack_id, diagnosisId: 'diagnosis', note: 'note',
      ...validPreconditions, expectedActiveToken, actorId: 'impostor' }, editorEndpoint))
      .toEqual({ status: 200, body: { ok: true, saved: true, workflowId: 'flow', packId: active.pack_id } });
    expect(save).toHaveBeenCalledOnce();
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'flow', spec, actorId: 'editor',
      expectedDraftAbsent: true, expectedActiveDeployNumber: 5, expectedActiveToken, diagnosisId: 'diagnosis', note: 'note' }));
    expect(drafts()).toHaveLength(1); expect(historyBytes()).toBe(before);
  });

  it('rejects a changed active snapshot without writing a draft', async () => {
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run('changed private contents');
    const before = historyBytes();
    const response = await post({ workflowId: 'flow', spec, ...validPreconditions, expectedActiveToken }, editorEndpoint);
    expect(response).toMatchObject({ status: 409, body: { code: 'BASELINE_STALE' } });
    expect(JSON.stringify(response.body)).not.toContain('changed private contents');
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it('accepts the unchanged save payload and acknowledges DB plus prepared Git, returning 200', async () => {
    const before = historyBytes();
    expect(await post()).toEqual({ status: 200, body: { ok: true, saved: true, workflowId: 'flow', packId: 'active-pack', ...preparedSaveIdentity } });
    expect(drafts()).toHaveLength(1);
    expect(await deps.workflowSpecRepo.findByWorkflowId('flow')).toMatchObject({ spec_json: JSON.stringify(spec),
      title: spec.title, owner_id: 'editor', pack_id: 'active-pack', version: null });
    expect(historyBytes()).toBe(before);
    expect(await post({ workflowId: 'flow', spec: { ...spec, title: 'Next draft' } })).toMatchObject({ status: 200 });
    expect(drafts()).toHaveLength(1); expect(historyBytes()).toBe(before);
    expect((await deps.workflowSpecRepo.findByWorkflowId('flow'))?.title).toBe('Next draft');
  });

  it('passes expectedDraftAbsent through and rejects requests arriving after another draft', async () => {
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 5 })).toMatchObject({ status: 200 });
    const winner = drafts();
    expect(await post({ workflowId: 'flow', spec: { ...spec, title: 'Must not overwrite' }, expectedDraftAbsent: true, expectedActiveDeployNumber: 5 }))
      .toMatchObject({ status: 409, body: { code: 'DRAFT_ALREADY_EXISTS' } });
    expect(drafts()).toEqual(winner);
  });

  it.each([false, null, 'true', 0, 1, {}, []])('rejects invalid expectedDraftAbsent=%j before querying Git', async expectedDraftAbsent => {
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent })).toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(drafts()).toEqual([]);
  });

  it.each([true, false])('rejects expectedDraftAbsent with createOnly=%s before querying Git', async createOnly => {
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, createOnly }))
      .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(drafts()).toEqual([]);
  });
  it('requires a positive integer active deploy number with expectedDraftAbsent', async () => {
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true }))
      .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 0 }))
      .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(await post({ workflowId: 'flow', spec, expectedActiveDeployNumber: 5 }))
      .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(drafts()).toEqual([]);
  });

  it.each([null, '', 'a'.repeat(63), 'z'.repeat(64), 1, {}, []])('rejects invalid expectedActiveToken=%j', async expectedActiveToken => {
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 5, expectedActiveToken }))
      .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    expect(drafts()).toEqual([]);
  });
  it('requires the matching draft-absence and deploy-number preconditions for an active token', async () => {
    for (const precondition of [{}, { expectedDraftAbsent: true }, { expectedActiveDeployNumber: 5 },
      { expectedDraftAbsent: true, expectedActiveDeployNumber: 5, createOnly: false }]) {
      expect(await post({ workflowId: 'flow', spec, ...precondition, expectedActiveToken: 'a'.repeat(64) }))
        .toMatchObject({ status: 400, body: { code: 'INVALID_REQUEST' } });
    }
    expect(drafts()).toEqual([]);
  });

  it('passes the active token through and rejects a same-number edit without disclosing its contents', async () => {
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run('private publication contents');
    const before = historyBytes();
    const result = await post({ workflowId: 'flow', spec, packId: active.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 5, expectedActiveToken });
    expect(result).toMatchObject({ status: 409, body: { code: 'BASELINE_STALE' } });
    expect(JSON.stringify(result.body)).not.toContain('private publication contents');
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });
  it.each(['initial read', 'transaction lock'])('maps ambiguous active text at %s to a sanitized conflict', async stage => {
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    const permissionsBefore = raw.prepare('SELECT * FROM bot_workflow_permissions').all();
    let afterWriter = '';
    const mutate = () => {
      raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run('private ambiguous text: \uFFFD');
      afterWriter = historyBytes();
    };
    if (stage === 'initial read') mutate();
    else {
      const transaction = db.transaction.bind(db);
      vi.spyOn(db, 'transaction').mockImplementationOnce(async fn => { mutate(); return transaction(fn); });
    }
    const result = await post({ workflowId: 'flow', spec, packId: active.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 5, expectedActiveToken });
    expect(result).toMatchObject({ status: 409, body: { error: 'Save could not be completed', code: 'BASELINE_STALE' } });
    expect(JSON.stringify(result.body)).not.toContain('private ambiguous text');
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(afterWriter);
    expect(raw.prepare('SELECT * FROM bot_workflow_permissions').all()).toEqual(permissionsBefore);
    expect((raw.prepare('SELECT spec_json FROM workflow_deploy_history WHERE is_active = 1').get() as { spec_json: string }).spec_json)
      .toBe('private ambiguous text: \uFFFD');
  });
  it('keeps legacy first-draft saves working with active text that cannot generate a token', async () => {
    raw.prepare('UPDATE workflow_deploy_history SET spec_json = ? WHERE is_active = 1').run('published \uFFFD text');
    const before = historyBytes();
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 5 }))
      .toMatchObject({ status: 200, body: { saved: true, packId: 'active-pack', ...preparedSaveIdentity } });
    expect(drafts()).toHaveLength(1); expect(historyBytes()).toBe(before);
  });

  it('does not misreport a database failure as an active token conflict', async () => {
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    const expectedActiveToken = editorActiveDeploymentToken('flow', active);
    vi.spyOn(deps.deployHistoryRepo, 'findActiveByWorkflowId').mockRejectedValueOnce(new Error('private database failure'));
    const result = await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 5, expectedActiveToken });
    expect(result).toMatchObject({ status: 500, body: { error: 'Save could not be completed', code: 'SAVE_FAILED' } });
    expect(JSON.stringify(result.body)).not.toContain('private database failure');
    expect(drafts()).toEqual([]);
  });

  it('accepts a matching token on the unchanged first-draft route', async () => {
    const active = (await deps.deployHistoryRepo.findActiveByWorkflowId('flow'))!;
    expect(await post({ workflowId: 'flow', spec, packId: active.pack_id, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 5, expectedActiveToken: editorActiveDeploymentToken('flow', active) }))
      .toMatchObject({ status: 200, body: { saved: true, packId: active.pack_id, ...preparedSaveIdentity } });
    expect(drafts()).toHaveLength(1);
  });

  it('rejects a stale active snapshot before inserting a first draft', async () => {
    raw.exec('UPDATE workflow_deploy_history SET deploy_number = 10, version = 10 WHERE is_active = 1');
    const before = historyBytes();
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 5 }))
      .toMatchObject({ status: 409, body: { code: 'BASELINE_STALE' } });
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before);
  });

  it('returns 404 when no active publication remains', async () => {
    raw.exec('UPDATE workflow_deploy_history SET is_active = 0');
    expect(await post()).toMatchObject({ status: 404, body: { code: 'WORKFLOW_NOT_FOUND' } });
    expect(drafts()).toEqual([]);
  });

  it('returns 403 for an authenticated user without edit permission', async () => {
    deps.resolvePrincipal = async () => ({ actorId: 'intruder' });
    expect(await post()).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(drafts()).toEqual([]);
  });

  it('requires a trusted actor instead of accepting one in the body', async () => {
    deps.resolvePrincipal = undefined;
    expect(await post({ workflowId: 'flow', spec, actorId: 'editor' })).toMatchObject({ status: 401 });
    expect(drafts()).toEqual([]);
  });

  it('returns 409 for a client-side pack rename', async () => {
    expect(await post({ workflowId: 'flow', spec, packId: 'other-pack' })).toMatchObject({ status: 409, body: { code: 'PACK_ID_MISMATCH' } });
    expect(drafts()).toEqual([]);
  });

  it.each([undefined, true] as const)('returns 409 for a concurrent draft (expectedDraftAbsent=%s)', async expectedDraftAbsent => {
    const transaction = db.transaction.bind(db);
    vi.spyOn(db, 'transaction').mockImplementationOnce(async fn => {
      await deps.workflowSpecRepo.upsert('flow', 'active-pack', JSON.stringify({ ...spec, title: 'Winner' }), 'other-editor');
      return transaction(fn);
    });
    expect(await post({ workflowId: 'flow', spec, expectedDraftAbsent, expectedActiveDeployNumber: expectedDraftAbsent ? 5 : undefined })).toMatchObject({ status: 409,
      body: { code: expectedDraftAbsent ? 'DRAFT_ALREADY_EXISTS' : 'WORKFLOW_ALREADY_EXISTS' } });
    expect(await deps.workflowSpecRepo.findByWorkflowId('flow')).toMatchObject({ title: 'Winner', owner_id: 'other-editor' });
  });

  it.each(['ABORT', 'IGNORE'])('returns a failure for an unsuccessful insert (%s), with no history change', async mode => {
    raw.exec(`CREATE TRIGGER fail_draft BEFORE INSERT ON workflow_specs BEGIN
      SELECT RAISE(${mode}${mode === 'ABORT' ? ", 'private database failure detail'" : ''}); END;`);
    const before = historyBytes();
    const result = await post();
    expect(result).toMatchObject({ status: 500, body: { error: 'Save could not be completed' } });
    expect(JSON.stringify(result.body)).not.toContain('private database failure detail');
    expect(drafts()).toEqual([]); expect(historyBytes()).toBe(before); expect(raw.inTransaction).toBe(false);
  });
  it.each(['CREATE_DB_OUTCOME_UNKNOWN', 'CREATE_DB_CONFIRMED_GIT_NOT_STARTED', 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN'])
  ('preserves create phase code %s in the public save response', async code => {
    vi.spyOn(AutoHealSaveService.prototype, 'save').mockRejectedValueOnce({ status: 503, code, requestId: 'create-request',
      branch: code === 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN' ? 'saved/flow' : undefined,
      commit: code === 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN' ? 'a'.repeat(40) : undefined, message: 'SECRET' });
    const response = await post();
    expect(response).toMatchObject({ status: 503, body: { code, requestId: 'create-request' } });
    expect(JSON.stringify(response.body)).not.toContain('SECRET');
    if (code === 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN') expect(response.body).toMatchObject({ branch: 'saved/flow', commit: 'a'.repeat(40) });
  });

});
