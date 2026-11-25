// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import { request as httpRequest, type Server } from 'node:http';
import Database from 'better-sqlite3';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { EditorDraftCasService } from '../../services/editor-draft-cas-service.js';
import { WorkflowUnifiedSaveService } from '../../services/workflow-unified-save-service.js';
import { withPreparedPackCandidateForUnifiedSave } from '../../services/pack-candidate-save.js';
import { createLocalRemote, runGit } from '../../services/__tests__/clawflows-git-client-fixture.js';
import { fingerprintWorkflowSnapshotSaveRequest } from '../../services/workflow-snapshot-save-request.js';
import { createWorkflowUnifiedSaveRouter, unifiedSaveHostGuard } from '../workflow-unified-save.js';
import { EventEmitter } from 'node:events';

const repositoryId = 'a'.repeat(64);
const spec = { id: 'flow', title: 'private workflow data', nodes: [{ id: 'n', executor: { type: 'agent' } }] };
const body = () => ({ requestId: 'request_123', sourceVersion: 'current', sourceCommit: 'b'.repeat(40),
  expectedSavedHead: null, expectedDraftToken: 'c'.repeat(64), specJson: JSON.stringify(spec), changes: [] });
const resolvePrincipal = vi.fn(), resolveAuthorizedService = vi.fn(), save = vi.fn();
const commit = 'd'.repeat(40), draftToken = 'e'.repeat(64);
const expectedHash = () => fingerprintWorkflowSnapshotSaveRequest({ ...body(), sourceVersion: 'current', actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId }).inputHash;
let server: Server, base: string;
async function start(preparse = false, guarded = false) {
  const app = express();
  // Test-only host guard; this is not an implementation or claim about a real host.
  app.use((req, res, next) => { if (req.headers['x-test-csrf'] !== 'allowed') res.sendStatus(403); else next(); });
  if (guarded) app.use(unifiedSaveHostGuard());
  if (preparse) app.use(express.json({ limit: '20mb' }));
  app.use('/workflows', createWorkflowUnifiedSaveRouter({ resolvePrincipal, resolveAuthorizedService }));
  server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('missing address');
  base = `http://127.0.0.1:${address.port}/workflows/flow/unified-save`;
}
const send = (value: unknown, options: { url?: string; raw?: boolean; headers?: Record<string, string> } = {}) => fetch(options.url ?? base, {
  method: 'POST', headers: { 'content-type': 'application/json', 'x-test-csrf': 'allowed', ...options.headers },
  body: options.raw ? String(value) : JSON.stringify(value),
});
beforeEach(async () => {
  resolvePrincipal.mockReset().mockResolvedValue({ actorId: 'editor' });
  save.mockReset().mockImplementation(async input => ({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed', inputHash: fingerprintWorkflowSnapshotSaveRequest(input).inputHash, commit, draftToken, private: 'secret' } }));
  resolveAuthorizedService.mockReset().mockResolvedValue({ packId: 'pack', repositoryId, service: { save } });
  await start();
});
afterEach(async () => { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); });

describe('unmounted unified save HTTP boundary', () => {
  it('uses the host principal and trusted workflow/Pack/repository binding only', async () => {
    const response = await send(body());
    expect(response.status).toBe(200); expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed', inputHash: expectedHash(), commit, draftToken }, reconciliationRequired: false });
    expect(resolveAuthorizedService).toHaveBeenCalledWith('flow', { actorId: 'editor' });
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toMatchObject({ actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId });
    expect(save.mock.calls[0][1]).toBe('editor');
  });
  it.each(['actorId', 'workflowId', 'packId', 'repositoryId', 'repo', 'remoteUrl', 'token', 'credential', 'extra'])('rejects forged or unknown field %s', async key => {
    expect((await send({ ...body(), [key]: 'attacker' })).status).toBe(400);
    expect(save).not.toHaveBeenCalled(); expect(resolveAuthorizedService).toHaveBeenCalledWith('flow', { actorId: 'editor' });
  });
  it('rejects nested unknown fields and duplicate keys instead of silently dropping them', async () => {
    expect((await send({ ...body(), changes: [{ operation: 'delete', path: 'a', credential: 'secret' }] })).status).toBe(400);
    expect((await send(JSON.stringify(body()).replace('"requestId":', '"requestId":"x","requestId":'), { raw: true })).status).toBe(400);
    expect(save).not.toHaveBeenCalled();
  });
  it('discovers the protocol only after authentication and a valid workflow binding', async () => {
    const get = () => fetch(base, { headers: { 'x-test-csrf': 'allowed' } });
    resolvePrincipal.mockResolvedValueOnce(null);
    expect((await get()).status).toBe(401);
    resolveAuthorizedService.mockResolvedValueOnce(null);
    expect((await get()).status).toBe(404);
    resolveAuthorizedService.mockResolvedValueOnce({ packId: 'pack', repositoryId: 'bad', service: { save } });
    expect((await get()).status).toBe(503);
    resolveAuthorizedService.mockResolvedValueOnce({ packId: 'pack', repositoryId: [repositoryId], service: { save } });
    expect((await get()).status).toBe(503);
    const response = await get(); expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual({ protocol: 'workflow-unified-save-v1' });
    expect(save).not.toHaveBeenCalled();
  });
  it('requires authenticated authorized identity, with no existence or data leak', async () => {
    resolvePrincipal.mockResolvedValueOnce(null);
    expect((await send(body())).status).toBe(401);
    resolveAuthorizedService.mockResolvedValueOnce(null);
    const response = await send(body()); expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ code: 'NOT_FOUND' }); expect(save).not.toHaveBeenCalled();
  });
  it('rejects unauthorized large bodies without waiting for or parsing any body bytes', async () => {
    resolveAuthorizedService.mockResolvedValueOnce(null);
    const result = await new Promise<{ status: number | undefined; text: string }>((resolve, reject) => {
      const req = httpRequest(base, { method: 'POST', headers: { 'content-type': 'application/json',
        'content-length': String(9 * 1024 * 1024), 'x-test-csrf': 'allowed' } }, response => {
        let text = ''; response.setEncoding('utf8'); response.on('data', part => { text += part; });
        response.on('end', () => { resolve({ status: response.statusCode, text }); req.destroy(); });
      });
      req.on('error', reject); req.setTimeout(3000, () => req.destroy(new Error('response waited for body')));
      req.flushHeaders(); // Deliberately send no body; parsing would wait.
    });
    expect(result.status).toBe(404); expect(JSON.parse(result.text)).toEqual({ code: 'NOT_FOUND' });
    expect(resolveAuthorizedService).toHaveBeenCalledOnce(); expect(save).not.toHaveBeenCalled();
  });
  it('fails closed for invalid identifiers, query arguments and host failures', async () => {
    expect((await send(body(), { url: base.replace('/flow/', '/bad..id/') })).status).toBe(400);
    expect((await send(body(), { url: `${base}?actorId=attacker` })).status).toBe(400);
    resolvePrincipal.mockRejectedValueOnce(new Error('private principal'));
    expect(await (await send(body())).json()).toEqual({ code: 'SERVICE_UNAVAILABLE' });
    resolveAuthorizedService.mockRejectedValueOnce(new Error('private config'));
    expect(await (await send(body())).json()).toEqual({ code: 'SERVICE_UNAVAILABLE' });
    expect(save).not.toHaveBeenCalled();
  });
  it('bounds raw body bytes, rejects compressed or non-JSON input and requires the host CSRF guard', async () => {
    expect((await send(' '.repeat(8 * 1024 * 1024 + 1), { raw: true })).status).toBe(413);
    expect((await send(body(), { headers: { 'content-encoding': 'gzip' } })).status).toBe(415);
    expect((await send(body(), { headers: { 'content-type': 'text/plain' } })).status).toBe(415);
    expect((await send(body(), { headers: { 'x-test-csrf': 'denied' } })).status).toBe(403);
    expect(save).not.toHaveBeenCalled();
  });
  it('fails closed when a host parser has already consumed the body', async () => {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await start(true); expect((await send(body())).status).toBe(400); expect(save).not.toHaveBeenCalled();
  });
  it.each([
    [{ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started', private: 'secret' } }, 'rejected', 'not_started', 'not_started', false],
    [{ outcome: 'unknown', evidence: { db: 'unknown', git: 'not_started' } }, 'unknown', 'unknown', 'not_started', true],
  ])('reports phases without sensitive evidence or retry %#', async (result, outcome, db, git, reconciliationRequired) => {
    save.mockResolvedValueOnce(result);
    const response = await send(body()); expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome, evidence: { db, git }, reconciliationRequired });
    expect(save).toHaveBeenCalledTimes(1);
  });
  it('returns correlation evidence for Git unknown without leaking other fields', async () => {
    save.mockResolvedValueOnce({ outcome: 'unknown', evidence: { db: 'confirmed', git: 'unknown',
      inputHash: expectedHash(), commit, draftToken, bundleBase64: 'secret bundle', path: 'secret path' } });
    const response = await send(body()); expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'unknown', evidence: {
      db: 'confirmed', git: 'unknown', inputHash: expectedHash(), commit, draftToken }, reconciliationRequired: true });
    expect(save).toHaveBeenCalledOnce();
  });
  it('treats partial DB claims without complete evidence as unknown', async () => {
    save.mockResolvedValueOnce({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
    const response = await send(body()); expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'unknown',
      evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
  });
  it('accepts a SHA-256 candidate and returns phase-appropriate partial evidence', async () => {
    const sha256 = 'f'.repeat(64);
    save.mockResolvedValueOnce({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started',
      inputHash: expectedHash(), commit: sha256, draftToken } });
    const response = await send(body()); expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'partial',
      evidence: { db: 'confirmed', git: 'not_started', inputHash: expectedHash(), commit: sha256, draftToken }, reconciliationRequired: true });
  });
  it('does not expose an invalid candidate in a Git unknown receipt', async () => {
    save.mockResolvedValueOnce({ outcome: 'unknown', evidence: { db: 'confirmed', git: 'unknown',
      inputHash: expectedHash(), commit: 'private/path', draftToken } });
    const response = await send(body()); expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'unknown',
      evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
  });
  it('does not expose candidate or token before DB confirmation', async () => {
    save.mockResolvedValueOnce({ outcome: 'unknown', evidence: { db: 'unknown', git: 'not_started', inputHash: expectedHash(), commit, draftToken } });
    const response = await send(body());
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'unknown',
      evidence: { db: 'unknown', git: 'not_started', inputHash: expectedHash() }, reconciliationRequired: true });
  });
  it.each([
    { commit: 'private/commit path' }, { commit: 'ABCDEF'.repeat(7) }, { commit: 'f'.repeat(41) },
    { inputHash: 'f'.repeat(64) }, { draftToken: 'private draft token' }, { commit: undefined },
  ])('does not leak malformed or mismatched evidence %#', async invalid => {
    save.mockResolvedValueOnce({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed', inputHash: expectedHash(), commit, draftToken, ...invalid } });
    const response = await send(body()); expect(response.status).toBe(409);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ requestId: 'request_123', outcome: 'unknown', evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
    expect(text).not.toContain('private'); expect(save).toHaveBeenCalledOnce();
  });
  it('treats thrown or contradictory service results as unknown and never exposes internals', async () => {
    save.mockRejectedValueOnce(new Error('secret git credential')).mockResolvedValueOnce({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'unknown' } });
    for (let i = 0; i < 2; i++) {
      const response = await send(body()); expect(response.status).toBe(409);
      expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'unknown', evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
    }
    expect(save).toHaveBeenCalledTimes(2);
  });
});

describe('HTTP with real SQLite, Git and experimental service', () => {
  it('enforces a real edit ACL and reports a lost Git acknowledgement as unknown after the writes', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'route-unified-'));
    let db: SqliteDatabase | undefined;
    try {
      const bare = createLocalRemote(root), seed = path.join(root, 'seed');
      mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
      writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
      writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\nnodes: []\n');
      runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'seed']);
      const current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
      const raw = new Database(':memory:');
      raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
        CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
        INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0), (2,NULL,'viewer','flow',1,0,0,'test',0,0);`);
      raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', 'pack', JSON.stringify({ ...spec, title: 'before' }), 'before', 1, 'editor', 1, 2);
      db = new SqliteDatabase(raw);
      const permissions = new BotWorkflowPermissionRepository(db), draft = new EditorDraftCasService(db, permissions);
      let pushes = 0, allowPack = false;
      const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
        withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
          pushes++; await push(); throw new Error('secret git acknowledgement');
        }));
      const service = new WorkflowUnifiedSaveService({ draft, repositoryId, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' },
        canEdit: (workflowId, actorId) => permissions.hasEditPermission(workflowId, actorId), canWritePack: async () => allowPack, prepare });
      resolveAuthorizedService.mockImplementation(async (workflowId, principal) => await permissions.hasEditPermission(workflowId, principal.actorId)
        ? { packId: 'pack', repositoryId, service } : null);
      const request = { ...body(), sourceVersion: 'draft', sourceCommit: current, expectedSavedHead: current, expectedDraftToken: (await draft.read('flow', 'editor')).draftToken };
      resolvePrincipal.mockResolvedValueOnce({ actorId: 'viewer' });
      expect((await send(request)).status).toBe(404);
      expect(pushes).toBe(0);
      const denied = await send(request);
      expect(await denied.json()).toEqual({ requestId: 'request_123', outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started', inputHash: fingerprintWorkflowSnapshotSaveRequest({ ...request, sourceVersion: 'draft', actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId }).inputHash }, reconciliationRequired: false });
      expect((raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get() as { spec_json: string }).spec_json).toContain('before');
      expect(pushes).toBe(0);
      allowPack = true;
      const response = await send(request); expect(response.status).toBe(409);
      const result = await response.json();
      expect(result).toEqual({ requestId: 'request_123', outcome: 'unknown', evidence: { db: 'confirmed', git: 'unknown', inputHash: fingerprintWorkflowSnapshotSaveRequest({ ...request, sourceVersion: 'draft', actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId }).inputHash, commit: expect.stringMatching(/^[a-f0-9]{40}$/), draftToken: expect.stringMatching(/^[a-f0-9]{64}$/) }, reconciliationRequired: true });
      expect(pushes).toBe(1);
      expect((raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get() as { spec_json: string }).spec_json).toContain('private workflow data');
      expect(runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow'])).toBe(result.evidence.commit);
      expect((await draft.read('flow', 'editor')).draftToken).toBe(result.evidence.draftToken);
    } finally { if (db) await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); }
  });
});


describe('host deadline', () => {
  it('does not share admission quota, and aborts on timeout or disconnect', () => {
    vi.useFakeTimers();
    try {
      const guard = unifiedSaveHostGuard();
      const response = () => {
        const res = new EventEmitter() as any;
        res.locals = {}; res.headersSent = false; res.destroyed = false;
        res.set = vi.fn().mockReturnValue(res);
        res.status = vi.fn().mockReturnValue(res);
        res.json = vi.fn().mockReturnValue(res);
        return res;
      };
      const before = response(), running = response(), disconnected = response(), next = vi.fn();
      guard({} as any, before, next); guard({} as any, running, next); guard({} as any, disconnected, next);
      running.locals.unifiedSaveRequestId = 'request_123';
      disconnected.emit('close');
      expect(disconnected.locals.unifiedSaveControl.signal.aborted).toBe(true);
      for (let i = 0; i < 70; i++) { const res = response(); guard({} as any, res, next); res.emit('finish'); }
      expect(next).toHaveBeenCalledTimes(73);
      vi.advanceTimersByTime(30_000);
      expect(before.status).toHaveBeenCalledWith(503);
      expect(running.status).toHaveBeenCalledWith(409);
      expect(before.locals.unifiedSaveControl.signal.aborted).toBe(true);
      expect(running.locals.unifiedSaveControl.signal.aborted).toBe(true);
      expect(running.json).toHaveBeenCalledWith({ requestId: 'request_123', outcome: 'unknown',
        evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
    } finally { vi.useRealTimers(); }
  });
});

it('does not let unauthenticated traffic exhaust a shared authenticated quota', async () => {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  await start(false, true);
  resolvePrincipal.mockResolvedValue(null);
  for (let i = 0; i < 65; i++) expect((await send(body())).status).toBe(401);
  resolvePrincipal.mockResolvedValue({ actorId: 'editor' });
  expect((await send(body())).status).toBe(200);
  expect(save).toHaveBeenCalledOnce();
});


it('does not advertise capability after its monotonic deadline', async () => {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  await start(false, true);
  resolvePrincipal.mockImplementationOnce(async req => {
    req.res.locals.unifiedSaveControl.deadline = 0;
    return { actorId: 'editor' };
  });
  const response = await fetch(base, { headers: { 'x-test-csrf': 'allowed' } });
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ code: 'SERVICE_UNAVAILABLE' });
  expect(save).not.toHaveBeenCalled();
});

it('does not authorize or write after an identity deadline has elapsed', async () => {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  await start(false, true);
  let finishIdentity!: (value: { actorId: string }) => void;
  resolvePrincipal.mockImplementationOnce(() => new Promise(resolve => { finishIdentity = resolve; }));
  let expire!: () => void;
  const original = globalThis.setTimeout;
  const spy = vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void, delay: number, ...args: unknown[]) => {
    if (delay === 30_000) expire = fn;
    return original(fn, delay, ...args);
  }) as typeof setTimeout);
  try {
    const pending = send(body());
    await vi.waitFor(() => { expect(finishIdentity).toBeTypeOf('function'); expect(expire).toBeTypeOf('function'); });
    expire();
    expect((await pending).status).toBe(503);
    finishIdentity({ actorId: 'editor' });
    await new Promise(resolve => original(resolve, 20));
    expect(resolveAuthorizedService).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled();
  } finally { spy.mockRestore(); }
});

it('reports a timed-out started save as unknown even if it later succeeds', async () => {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  await start(false, true);
  let finishSave!: (result: unknown) => void;
  save.mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve; }));
  let expire!: () => void;
  const original = globalThis.setTimeout;
  const spy = vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void, delay: number, ...args: unknown[]) => {
    if (delay === 30_000) expire = fn;
    return original(fn, delay, ...args);
  }) as typeof setTimeout);
  try {
    const pending = send(body());
    await vi.waitFor(() => { expect(finishSave).toBeTypeOf('function'); expect(expire).toBeTypeOf('function'); });
    expire();
    const response = await pending;
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ requestId: 'request_123', outcome: 'unknown',
      evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
    finishSave({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed', inputHash: expectedHash(), commit, draftToken } });
    await new Promise(resolve => original(resolve, 20));
    expect(save).toHaveBeenCalledOnce();
  } finally { spy.mockRestore(); }
});

it.each([[1, 'abort'], [2, 'abort'], [1, 'deadline'], [2, 'deadline']] as const)(
  'after %s Pack authorization, %s prevents the next write', async (delayedAuthorization, mechanism) => {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  await start(false, true);
  const root = mkdtempSync(path.join(tmpdir(), 'route-deadline-'));
  let db: SqliteDatabase | undefined;
  let finishAuthorization: ((value: boolean) => void) | undefined;
  const original = globalThis.setTimeout;
  let expire!: () => void;
  const spy = vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void, delay: number, ...args: unknown[]) => {
    if (delay === 30_000) expire = fn;
    return original(fn, delay, ...args);
  }) as typeof setTimeout);
  try {
    const bare = createLocalRemote(root), seed = path.join(root, 'seed');
    mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
    writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\nnodes: []\n');
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'seed']);
    const current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const raw = new Database(':memory:');
    raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
      INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0);`);
    const before = JSON.stringify({ ...spec, title: 'before' });
    raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', 'pack', before, 'before', 1, 'editor', 1, 2);
    db = new SqliteDatabase(raw);
    const permissions = new BotWorkflowPermissionRepository(db), draft = new EditorDraftCasService(db, permissions);
    let aclCalls = 0, writes = 0, pushes = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => { pushes++; return push(); }));
    const service = new WorkflowUnifiedSaveService({
      draft: { read: draft.read.bind(draft), save: input => { writes++; return draft.save(input); } },
      repositoryId, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' },
      canEdit: (workflowId, actorId) => permissions.hasEditPermission(workflowId, actorId),
      canWritePack: async () => ++aclCalls === delayedAuthorization
        ? new Promise<boolean>(resolve => { finishAuthorization = resolve; }) : true,
      prepare,
    });
    let finished: ReturnType<typeof service.save> | undefined;
    let control: { signal: AbortSignal; deadline: number } | undefined;
    resolveAuthorizedService.mockResolvedValue({ packId: 'pack', repositoryId, service: { save: (...args: Parameters<typeof service.save>) => {
      control = args[2]; finished = service.save(...args); return finished;
    } } });
    const request = { ...body(), sourceVersion: 'draft', sourceCommit: current, expectedSavedHead: current, expectedDraftToken: (await draft.read('flow', 'editor')).draftToken };
    const pending = send(request);
    await vi.waitFor(() => { expect(finishAuthorization).toBeTypeOf('function'); expect(expire).toBeTypeOf('function'); }, { timeout: 15_000 });
    expect(writes).toBe(delayedAuthorization - 1);
    if (mechanism === 'abort') {
      expire();
      const response = await pending; expect(response.status).toBe(409);
      expect(await response.json()).toMatchObject({ outcome: 'unknown', reconciliationRequired: true });
    } else {
      // Simulate a delayed timer: elapsed monotonic deadline must suffice even
      // though the AbortSignal has not fired yet.
      expect(control!.signal.aborted).toBe(false);
      control!.deadline = 0;
    }
    finishAuthorization!(true);
    const result = await finished!;
    if (mechanism === 'deadline') {
      const response = await pending; expect(response.status).toBe(409);
      expect(await response.json()).toMatchObject({ outcome: delayedAuthorization === 1 ? 'rejected' : 'partial' });
    }
    expect(result).toMatchObject({ outcome: delayedAuthorization === 1 ? 'rejected' : 'partial',
      evidence: { db: delayedAuthorization === 1 ? 'not_started' : 'confirmed', git: 'not_started' } });
    expect(writes).toBe(delayedAuthorization - 1); expect(pushes).toBe(0);
    const row = raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get() as { spec_json: string };
    expect(JSON.parse(row.spec_json)).toEqual(delayedAuthorization === 1 ? JSON.parse(before) : spec);
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow'])).toBe(current);
  } finally {
    finishAuthorization?.(false); spy.mockRestore();
    if (db) await db.close(); rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
}, 20_000);
