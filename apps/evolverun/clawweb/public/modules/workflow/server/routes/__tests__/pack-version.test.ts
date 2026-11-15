// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { afterEach, expect, it, vi } from 'vitest';
import { createPackVersionRouter } from '../pack-version.js';
import { PackOperationError } from '../../services/pack-operation-service.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError } from '../../services/pack-candidate-save.js';
const sha = 'a'.repeat(40);
function deps() {
  return {
    resolvePrincipal: vi.fn(async (_req: express.Request): Promise<{ actorId: string } | null> => ({ actorId: 'trusted' })),
    botPermRepo: { hasEditPermission: vi.fn(async () => true), hasExecutePermission: vi.fn(async () => false) },
    workflowSpecRepo: { findByWorkflowId: vi.fn(async () => ({ workflow_id: 'flow', pack_id: 'pack' })) },
    readSnapshot: vi.fn(async () => ({ workflowId: 'flow', packId: 'historical', baseCommit: sha, baseRef: 'refs/tags/deploy/flow/#1', files: [], draftHead: null, versionFiles: [{ path: 'x.bin', contentBase64: 'AP8=', mode: '100644' as const }] })),
    saveCandidate: vi.fn(async () => ({ branch: 'wf/flow', commit: sha })),
    readOperation: vi.fn(async (_requestId: string, _workflowId: string, _actorId: string) => ({ requestId: 'request-status-01', kind: 'save', state: 'unknown' })),
    reconcileOperation: vi.fn(async (_requestId: string, _workflowId: string, _actorId: string) => ({ requestId: 'request-status-01', kind: 'save', state: 'completed' })),
  };
}
let server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => { if (server) await new Promise<void>((resolve, reject) => server!.close(e => e ? reject(e) : resolve())); server = undefined; });
async function fixture(d: ReturnType<typeof deps>) {
  const app = express(); app.use(express.json()); app.use('/api/workflows', createPackVersionRouter(d));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('address');
  return `http://127.0.0.1:${address.port}/api/workflows/flow/pack-version`;
}
const input = { sourceVersion: 'current', sourceCommit: sha, expectedSavedHead: null, changes: [{ path: 'a', operation: 'create', contentBase64: 'AA==', mode: '100644' }] };
const put = (url: string, body: unknown) => fetch(url, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
it('requires trusted authentication and edit permission before reading', async () => {
  const d = deps(); const url = await fixture(d); d.resolvePrincipal.mockResolvedValue(null);
  expect((await fetch(`${url}?version=current`, { headers: { 'x-user-id': 'admin', cookie: 'user=admin' } })).status).toBe(401);
  expect(d.botPermRepo.hasEditPermission).not.toHaveBeenCalled();
  d.resolvePrincipal.mockResolvedValue({ actorId: 'trusted' }); d.botPermRepo.hasEditPermission.mockResolvedValue(false);
  expect((await fetch(`${url}?version=current`)).status).toBe(403); expect(d.readSnapshot).not.toHaveBeenCalled();
});
it('returns the full inventory and enforces version and trusted repository selection', async () => {
  const d = deps(); const url = await fixture(d);
  const r = await fetch(`${url}?version=current`); expect(r.status).toBe(200); expect(r.headers.get('cache-control')).toBe('no-store');
  expect(await r.json()).toMatchObject({ canWrite: true, versionFiles: [{ path: 'x.bin', contentBase64: 'AP8=', mode: '100644' }] });
  expect(d.readSnapshot).toHaveBeenCalledWith('flow', 'pack', 'current');
  expect((await fetch(`${url}?version=current&packId=evil`)).status).toBe(400);
  expect((await fetch(`${url}?version=draft`)).status).toBe(503);
});
it('rejects forged authority, unsafe changes and obsolete operations before saving', async () => {
  const d = deps(); const url = await fixture(d);
  for (const body of [{ ...input, repo: 'evil' }, { ...input, changes: [{ ...input.changes[0], operation: 'upsert' }] }, { ...input, changes: [{ ...input.changes[0], path: '../secret' }] }, { ...input, changes: [{ ...input.changes[0], ref: 'evil' }] }]) expect((await put(url, body)).status).toBe(400);
  expect(d.saveCandidate).not.toHaveBeenCalled();
  expect((await put(url, input)).status).toBe(200); expect(d.saveCandidate).toHaveBeenCalledWith({ actorId: 'trusted', workflowId: 'flow', ...input });
});
it('passes the observed saved head for current edits even when release and saved commits differ', async () => {
  const d = deps(); const url = await fixture(d);
  const request = { ...input, requestId: 'save-current-release', expectedSavedHead: 'b'.repeat(40) };
  expect((await put(url, request)).status).toBe(200);
  expect(d.saveCandidate).toHaveBeenCalledExactlyOnceWith({ actorId: 'trusted', workflowId: 'flow', ...request });
  // CAS belongs to the callback: a genuinely moved saved head must still surface as a conflict.
  d.saveCandidate.mockRejectedValueOnce(new PackCandidateConflictError());
  const conflict = await put(url, request);
  expect(conflict.status).toBe(409); expect((await conflict.json()).code).toBe('VERSION_CONFLICT');
  expect(d.saveCandidate).toHaveBeenCalledTimes(2);
  expect(d.readSnapshot).not.toHaveBeenCalled();
});
it('maps conflicts and sanitizes operational failures', async () => {
  const d = deps(); const url = await fixture(d);
  d.saveCandidate.mockRejectedValueOnce(new PackCandidateConflictError()).mockRejectedValueOnce(new PackCandidateOutcomeUnknownError('wf/flow', sha)).mockRejectedValueOnce(new Error('secret URL/token'));
  expect((await put(url, input)).status).toBe(409);
  const unknown = await put(url, input); expect(unknown.status).toBe(503); expect(await unknown.json()).toEqual({ error: 'Save outcome unknown; inspect the Git branch manually before retrying', code: 'SAVE_OUTCOME_UNKNOWN', branch: 'wf/flow', commit: sha });
  const r = await put(url, input); expect(r.status).toBe(503); expect(await r.text()).not.toContain('secret');
});
it('fails closed when the host does not wire saving', async () => {
  const d = deps(); const app = express(); app.use(express.json()); app.use(createPackVersionRouter({ ...d, saveCandidate: undefined }));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening'); const address = server.address();
  if (!address || typeof address === 'string') throw new Error('address');
  const url = `http://127.0.0.1:${address.port}/flow/pack-version`;
  const read = await fetch(`${url}?version=current`);
  expect(read.status).toBe(200); expect((await read.json()).canWrite).toBe(false);
  const r = await put(url, input);
  expect(r.status).toBe(503); expect(d.saveCandidate).not.toHaveBeenCalled();
});

it('authorizes writes using only the trusted principal before touching Git', async () => {
  const d = deps(); const url = await fixture(d);
  d.resolvePrincipal.mockResolvedValue(null);
  expect((await put(url, input)).status).toBe(401);
  expect(d.botPermRepo.hasEditPermission).not.toHaveBeenCalled();
  d.resolvePrincipal.mockResolvedValue({ actorId: 'trusted' });
  d.botPermRepo.hasEditPermission.mockResolvedValue(false);
  expect((await put(url, input)).status).toBe(403);
  expect(d.workflowSpecRepo.findByWorkflowId).not.toHaveBeenCalled();
  expect(d.saveCandidate).not.toHaveBeenCalled();
  expect(d.botPermRepo.hasEditPermission).toHaveBeenCalledWith('flow', 'trusted');
});

it('passes the stable request ID and server actor to save, rejecting forged identity', async () => {
  const d = deps(); const url = await fixture(d);
  const requestId = 'save-request-001';
  expect((await put(url, { ...input, requestId, actorId: 'admin' })).status).toBe(400);
  for (const invalid of ['', 'short', '../request', 'a'.repeat(129), 123, null]) {
    expect((await put(url, { ...input, requestId: invalid })).status).toBe(400);
  }
  expect(d.saveCandidate).not.toHaveBeenCalled();
  expect((await put(url, { ...input, requestId })).status).toBe(200);
  expect(d.saveCandidate).toHaveBeenCalledExactlyOnceWith({ ...input, requestId, actorId: 'trusted', workflowId: 'flow' });
});

it('exposes the journal recovery identity without leaking operational error details', async () => {
  const d = deps(); const url = await fixture(d);
  const requestId = 'save-unknown-001';
  const error = new PackOperationError(503, 'SAVE_OUTCOME_UNKNOWN', requestId, 'wf/flow', sha);
  error.message = 'secret remote URL, credentials and bundle';
  d.saveCandidate.mockRejectedValueOnce(error);
  const r = await put(url, { ...input, requestId });
  expect(r.status).toBe(503); expect(r.headers.get('cache-control')).toBe('no-store');
  expect(await r.json()).toEqual({ error: 'Workflow operation could not be completed', code: 'SAVE_OUTCOME_UNKNOWN',
    requestId, branch: 'wf/flow', commit: sha });
});

it('keeps authenticated status reads separate from explicit reconciliation', async () => {
  const d = deps(); const url = (await fixture(d)).replace('pack-version', 'pack-operations/request-status-01');
  const read = await fetch(url);
  expect(read.status).toBe(200); expect(read.headers.get('cache-control')).toBe('no-store');
  expect(await read.json()).toMatchObject({ requestId: 'request-status-01', state: 'unknown' });
  expect(d.readOperation).toHaveBeenCalledExactlyOnceWith('request-status-01', 'flow', 'trusted');
  expect(d.reconcileOperation).not.toHaveBeenCalled();
  expect((await fetch(`${url}/reconcile`)).status).toBe(405);
  expect(d.reconcileOperation).not.toHaveBeenCalled();
  expect((await fetch(`${url}/reconcile`, { method: 'POST' })).status).toBe(200);
  expect(d.reconcileOperation).toHaveBeenCalledExactlyOnceWith('request-status-01', 'flow', 'trusted');
});

it('requires authentication and edit permission for save status and reconciliation', async () => {
  const d = deps(); const url = (await fixture(d)).replace('pack-version', 'pack-operations/request-status-01');
  d.resolvePrincipal.mockResolvedValue(null);
  expect((await fetch(url)).status).toBe(401);
  expect((await fetch(`${url}/reconcile`, { method: 'POST' })).status).toBe(401);
  expect(d.readOperation).not.toHaveBeenCalled(); expect(d.reconcileOperation).not.toHaveBeenCalled();
  d.resolvePrincipal.mockResolvedValue({ actorId: 'trusted' }); d.botPermRepo.hasEditPermission.mockResolvedValue(false);
  expect((await fetch(url)).status).toBe(403);
  expect((await fetch(`${url}/reconcile`, { method: 'POST' })).status).toBe(403);
  expect(d.readOperation).toHaveBeenCalledTimes(2);
  expect(d.readOperation).toHaveBeenLastCalledWith('request-status-01', 'flow', 'trusted');
  expect(d.reconcileOperation).not.toHaveBeenCalled();
});

it('allows execute-only users to read and reconcile their own deploy, but not files or save operations', async () => {
  const d = deps(); const fileUrl = await fixture(d);
  const operationUrl = fileUrl.replace('pack-version', 'pack-operations/request-status-01');
  d.botPermRepo.hasEditPermission.mockResolvedValue(false);
  d.botPermRepo.hasExecutePermission.mockResolvedValue(true);
  const deployReceipt = { requestId: 'request-status-01', kind: 'deploy', state: 'unknown' };
  d.readOperation.mockResolvedValue(deployReceipt);
  d.reconcileOperation.mockResolvedValue({ ...deployReceipt, state: 'completed' });
  const read = await fetch(operationUrl);
  expect(read.status).toBe(200); expect(await read.json()).toEqual(deployReceipt);
  expect(d.readOperation).toHaveBeenCalledExactlyOnceWith('request-status-01', 'flow', 'trusted');
  expect(d.reconcileOperation).not.toHaveBeenCalled();
  const recovered = await fetch(`${operationUrl}/reconcile`, { method: 'POST' });
  expect(recovered.status).toBe(200); expect(await recovered.json()).toEqual({ ...deployReceipt, state: 'completed' });
  expect(d.readOperation).toHaveBeenCalledTimes(2);
  expect(d.reconcileOperation).toHaveBeenCalledExactlyOnceWith('request-status-01', 'flow', 'trusted');
  expect(d.botPermRepo.hasExecutePermission).toHaveBeenCalledWith('flow', 'trusted');
  for (const version of ['current', 'draft']) expect((await fetch(`${fileUrl}?version=${version}`)).status).toBe(403);
  expect((await put(fileUrl, input)).status).toBe(403);
  expect(d.readSnapshot).not.toHaveBeenCalled(); expect(d.saveCandidate).not.toHaveBeenCalled();
  d.readOperation.mockResolvedValue({ ...deployReceipt, kind: 'save' });
  expect((await fetch(operationUrl)).status).toBe(403);
  expect((await fetch(`${operationUrl}/reconcile`, { method: 'POST' })).status).toBe(403);
  expect(d.reconcileOperation).toHaveBeenCalledTimes(1);
});

it.each([
  { actorId: 'another-actor', workflowId: 'flow' },
  { actorId: 'trusted', workflowId: 'another-workflow' },
])('honors scoped reader rejection before deploy recovery for %j', async scope => {
  const d = deps(); const fileUrl = await fixture(d);
  d.resolvePrincipal.mockResolvedValue({ actorId: scope.actorId });
  d.botPermRepo.hasEditPermission.mockResolvedValue(false); d.botPermRepo.hasExecutePermission.mockResolvedValue(true);
  d.readOperation.mockImplementation(async (_requestId, workflowId, actorId) => {
    if (workflowId !== 'flow' || actorId !== 'trusted') throw new PackOperationError(404, 'OPERATION_NOT_FOUND');
    return { requestId: 'request-status-01', kind: 'deploy', state: 'unknown' };
  });
  const url = fileUrl.replace('/flow/pack-version', `/${scope.workflowId}/pack-operations/request-status-01`);
  for (const response of [await fetch(url), await fetch(`${url}/reconcile`, { method: 'POST' })]) {
    expect(response.status).toBe(404); expect((await response.json()).code).toBe('OPERATION_NOT_FOUND');
  }
  expect(d.readOperation).toHaveBeenCalledTimes(2);
  expect(d.readOperation).toHaveBeenLastCalledWith('request-status-01', scope.workflowId, scope.actorId);
  expect(d.botPermRepo.hasEditPermission).not.toHaveBeenCalled();
  expect(d.botPermRepo.hasExecutePermission).not.toHaveBeenCalled();
  expect(d.reconcileOperation).not.toHaveBeenCalled();
});

it('denies deploy recovery when neither execute nor edit is granted and allows edit-only recovery', async () => {
  const d = deps(); const url = (await fixture(d)).replace('pack-version', 'pack-operations/request-status-01');
  d.readOperation.mockResolvedValue({ requestId: 'request-status-01', kind: 'deploy', state: 'unknown' });
  d.botPermRepo.hasEditPermission.mockResolvedValue(false);
  expect((await fetch(url)).status).toBe(403);
  expect((await fetch(`${url}/reconcile`, { method: 'POST' })).status).toBe(403);
  expect(d.reconcileOperation).not.toHaveBeenCalled();
  d.botPermRepo.hasEditPermission.mockResolvedValue(true);
  expect((await fetch(url)).status).toBe(200);
  expect((await fetch(`${url}/reconcile`, { method: 'POST' })).status).toBe(200);
  expect(d.reconcileOperation).toHaveBeenCalledExactlyOnceWith('request-status-01', 'flow', 'trusted');
});

it.each(['Pack manifest conflict', 'Mixed Pack layout'])('reports %s as a safe snapshot conflict', async message => {
  const d = deps(); const url = await fixture(d);
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    d.readSnapshot.mockRejectedValueOnce(new Error(message));
    const response = await fetch(`${url}?version=current`);
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'Version snapshot conflict', code: 'SNAPSHOT_CONFLICT' });
    expect(log).toHaveBeenCalledWith(expect.stringContaining('PACK_MANIFEST_CONFLICT'));
    expect(log).toHaveBeenCalledWith(expect.stringContaining('"phase":"snapshot"'));
  } finally { log.mockRestore(); }
});

it('records only a fixed failure category and phase without raw identity or provider errors', async () => {
  const d = deps(); const url = await fixture(d);
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    d.resolvePrincipal.mockRejectedValueOnce(Object.assign(new Error('secret-token /private/path'), { code: 'secret-provider-code' }));
    const response = await fetch(`${url}?version=current`);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' });
    const serialized = JSON.stringify(log.mock.calls);
    expect(log).toHaveBeenCalledWith(expect.stringContaining('"phase":"identity"'));
    expect(serialized).toContain('PACK_READ_FAILED');
    expect(serialized).not.toMatch(/secret|private\/path/);
  } finally { log.mockRestore(); }
});

it('logs a safe ref-change category without leaking the underlying exception', async () => {
  const d = deps(); const url = await fixture(d);
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    d.readSnapshot.mockRejectedValueOnce(Object.assign(new Error('secret-ref-detail'), { code: 'PACK_REFS_CHANGED' }));
    const response = await fetch(`${url}?version=current`);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' });
    expect(log).toHaveBeenCalledWith(expect.stringContaining('PACK_REFS_CHANGED'));
    expect(JSON.stringify(log.mock.calls)).not.toContain('secret-ref-detail');
  } finally { log.mockRestore(); }
});
