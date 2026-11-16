// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPackScriptsRouter } from '../pack-scripts.js';
import { createPackVersionRouter } from '../pack-version.js';
import type { PackScriptVersion } from '../../services/pack-script-snapshot.js';

const sha = 'a'.repeat(40);
const routes = ['pack-version', 'pack-scripts'] as const;
type Route = typeof routes[number];
const forbidden = { error: 'Forbidden', code: 'FORBIDDEN' };
function snapshot(version: PackScriptVersion = 'draft') {
  return { workflowId: 'flow', packId: 'pack', baseCommit: sha,
    baseRef: version === 'current' ? 'refs/tags/deploy/flow/#1' : 'origin/wf/flow', draftHead: sha,
    files: [{ path: 'scripts/run.sh', content: 'private script', mode: '100644' as const }],
    versionFiles: [{ path: 'scripts/run.sh', contentBase64: Buffer.from('private script').toString('base64'), mode: '100644' as const }] };
}
function dependencies() {
  return {
    resolvePrincipal: vi.fn(async (_req: express.Request): Promise<{ actorId: string } | null> => ({ actorId: 'trusted' })),
    authorizeRead: vi.fn(async (_workflowId: string, _actorId: string) => true),
    botPermRepo: { hasEditPermission: vi.fn(async (_workflowId: string, _actorId: string) => false),
      hasExecutePermission: vi.fn(async (_workflowId: string, _actorId: string) => false) },
    workflowSpecRepo: { findByWorkflowId: vi.fn(async (_workflowId: string): Promise<{ workflow_id: string; pack_id: string | null } | null> => ({ workflow_id: 'flow', pack_id: 'pack' })) },
    authorizePackRead: vi.fn(async (_workflowId: string, _packId: string, _actorId: string) => true),
    supportsVersionedSnapshots: true,
    supportsVersionedPackResolution: true,
    readSnapshot: vi.fn(async (_workflowId: string, _packId: string, version?: PackScriptVersion) => snapshot(version)),
    readActiveSnapshot: vi.fn(async (_workflowId: string, version: PackScriptVersion) => snapshot(version)),
    saveCandidate: vi.fn(async () => ({ branch: 'wf/flow', commit: sha })),
    readOperation: vi.fn(async (_requestId: string, _workflowId: string, _actorId: string) => ({ kind: 'save', state: 'unknown' })),
    reconcileOperation: vi.fn(async () => ({ kind: 'save', state: 'completed' })),
  };
}
let server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()));
  server = undefined; vi.restoreAllMocks();
});
async function fixture(route: Route, deps: ReturnType<typeof dependencies>, overrides: Partial<ReturnType<typeof dependencies>> = {}) {
  const app = express(); app.use(express.json());
  const configured = { ...deps, ...overrides };
  app.use('/api/workflows', route === 'pack-version' ? createPackVersionRouter(configured) : createPackScriptsRouter(configured));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('No address');
  return `http://127.0.0.1:${address.port}/api/workflows/flow/${route}`;
}
function noDataReads(deps: ReturnType<typeof dependencies>) {
  expect(deps.workflowSpecRepo.findByWorkflowId).not.toHaveBeenCalled();
  expect(deps.readSnapshot).not.toHaveBeenCalled(); expect(deps.readActiveSnapshot).not.toHaveBeenCalled();
}

for (const route of routes) describe(`${route} explicit authenticated read policy`, () => {
  it.each(['current', 'draft'] as const)('allows a verified non-editor to read %s without granting writes', async version => {
    const deps = dependencies(); const url = await fixture(route, deps);
    const response = await fetch(`${url}?version=${version}`, { headers: { 'x-user-id': 'forged', cookie: 'user=forged' } });
    expect(response.status).toBe(200); expect(response.headers.get('cache-control')).toBe('no-store');
    const body = await response.json(); expect(body).toMatchObject(snapshot(version));
    if (route === 'pack-version') expect(body.canWrite).toBe(false);
    expect(deps.authorizeRead).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    expect(deps.readSnapshot).toHaveBeenCalledExactlyOnceWith('flow', 'pack', version);
    expect(deps.resolvePrincipal.mock.invocationCallOrder[0]).toBeLessThan(deps.authorizeRead.mock.invocationCallOrder[0]);
    expect(deps.authorizeRead.mock.invocationCallOrder[0]).toBeLessThan(deps.workflowSpecRepo.findByWorkflowId.mock.invocationCallOrder[0]);
    expect(deps.authorizeRead.mock.invocationCallOrder[0]).toBeLessThan(deps.readSnapshot.mock.invocationCallOrder[0]);
    if (route === 'pack-version') {
      expect(deps.botPermRepo.hasEditPermission).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
      expect(deps.authorizeRead.mock.invocationCallOrder[0]).toBeLessThan(deps.botPermRepo.hasEditPermission.mock.invocationCallOrder[0]);
    } else expect(deps.authorizePackRead).toHaveBeenCalledExactlyOnceWith('flow', 'pack', 'trusted');
    expect(deps.saveCandidate).not.toHaveBeenCalled(); expect(deps.reconcileOperation).not.toHaveBeenCalled();
  });

  it('permits host-authorized reads without an edit provider, never write capability', async () => {
    const deps = dependencies(); const url = await fixture(route, deps, { botPermRepo: null });
    const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(200); const body = await response.json();
    if (route === 'pack-version') expect(body.canWrite).toBe(false);
    expect(deps.authorizeRead).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
  });

  it.each([null, { actorId: '' }, { actorId: '   ' }])('rejects missing or empty verified principal %j before any policy or data access', async principal => {
    const deps = dependencies(); deps.resolvePrincipal.mockResolvedValue(principal);
    const url = await fixture(route, deps); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(401); expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual({ error: 'Authentication required', code: 'AUTHENTICATION_REQUIRED' });
    expect(deps.authorizeRead).not.toHaveBeenCalled(); expect(deps.botPermRepo.hasEditPermission).not.toHaveBeenCalled();
    expect(deps.authorizePackRead).not.toHaveBeenCalled(); noDataReads(deps);
  });

  it('denies an explicit false policy even for editors, before ACL, DB or Git access', async () => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockResolvedValue(true); deps.authorizeRead.mockResolvedValue(false);
    const url = await fixture(route, deps); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(403); expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual(forbidden);
    expect(deps.authorizeRead).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    expect(deps.botPermRepo.hasEditPermission).not.toHaveBeenCalled(); expect(deps.authorizePackRead).not.toHaveBeenCalled(); noDataReads(deps);
  });

  it.each(['plain', 'structured'])('sanitizes %s rejected policy promises without ACL, DB or Git fallback', async kind => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockResolvedValue(true);
    const error = new Error('secret-token /private/repository private script');
    deps.authorizeRead.mockRejectedValue(kind === 'plain' ? error : Object.assign(error, {
      status: 503, code: 'secret-token', requestId: 'private-policy-request', branch: 'private/repository', commit: sha,
    }));
    const log = vi.spyOn(console, 'info').mockImplementation(() => {});
    const url = await fixture(route, deps); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(503); expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' });
    expect(JSON.stringify(log.mock.calls)).not.toMatch(/secret-token|private\/repository|private script/);
    expect(deps.authorizeRead).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    expect(deps.botPermRepo.hasEditPermission).not.toHaveBeenCalled(); expect(deps.authorizePackRead).not.toHaveBeenCalled(); noDataReads(deps);
  });

  it('requires an exact true policy result', async () => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockResolvedValue(true);
    deps.authorizeRead.mockResolvedValue('yes' as unknown as boolean);
    const url = await fixture(route, deps); expect((await fetch(`${url}?version=draft`)).status).toBe(403); noDataReads(deps);
  });

  it.each([false, true])('keeps the default read ACL when edit=%s and the host omits its policy', async edit => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockResolvedValue(edit);
    const url = await fixture(route, deps, { authorizeRead: undefined }); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(edit ? 200 : 403); expect(deps.authorizeRead).not.toHaveBeenCalled();
    expect(deps.botPermRepo.hasEditPermission).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    if (!edit) noDataReads(deps);
    else if (route === 'pack-version') expect((await response.json()).canWrite).toBe(true);
  });

  it('keeps default reads denied without an edit provider', async () => {
    const deps = dependencies(); const url = await fixture(route, deps, { authorizeRead: undefined, botPermRepo: null });
    expect((await fetch(`${url}?version=draft`)).status).toBe(403); noDataReads(deps);
  });

  it('keeps default reads failed closed when the edit lookup rejects', async () => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockRejectedValue(new Error('private ACL detail'));
    const url = await fixture(route, deps, { authorizeRead: undefined }); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(503); expect(await response.text()).not.toContain('private ACL detail'); noDataReads(deps);
  });

  it('validates workflow IDs before the read policy', async () => {
    const deps = dependencies(); const url = (await fixture(route, deps)).replace('/flow/', '/bad..id/');
    expect((await fetch(`${url}?version=draft`)).status).toBe(400); expect(deps.authorizeRead).not.toHaveBeenCalled(); noDataReads(deps);
  });

  it('does not let a read grant bypass the selected snapshot ref validation', async () => {
    const deps = dependencies(); deps.readSnapshot.mockResolvedValue({ ...snapshot(), baseRef: 'origin/wf/other' });
    const url = await fixture(route, deps); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(503); expect(response.headers.get('cache-control')).toBe('no-store');
    const body = await response.text(); expect(body).not.toMatch(/private script|origin\/wf\/other|cHJpdmF0ZSBzY3JpcHQ=/);
  });
});

describe('pack version read/write separation', () => {
  it.each(['current', 'draft'] as const)('also authorizes active-only %s snapshots without edit access', async version => {
    const deps = dependencies(); deps.workflowSpecRepo.findByWorkflowId.mockResolvedValue(null);
    const url = await fixture('pack-version', deps); const response = await fetch(`${url}?version=${version}`);
    expect(response.status).toBe(200); expect(await response.json()).toMatchObject({ ...snapshot(version), canWrite: false });
    expect(deps.readActiveSnapshot).toHaveBeenCalledExactlyOnceWith('flow', version); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });

  it.each([true, false])('reports canWrite only from actual edit ACL and save availability (%s)', async saving => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockResolvedValue(true);
    const url = await fixture('pack-version', deps, { saveCandidate: saving ? deps.saveCandidate : undefined });
    const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(200); expect((await response.json()).canWrite).toBe(saving);
    expect(deps.botPermRepo.hasEditPermission).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
  });

  it('preserves an authorized read with canWrite false when the independent edit ACL rejects', async () => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockRejectedValue(new Error('private ACL detail'));
    const log = vi.spyOn(console, 'info').mockImplementation(() => {});
    const url = await fixture('pack-version', deps); const response = await fetch(`${url}?version=draft`);
    expect(response.status).toBe(200); expect(await response.json()).toMatchObject({ ...snapshot(), canWrite: false });
    expect(deps.authorizeRead).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    expect(deps.botPermRepo.hasEditPermission).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    expect(JSON.stringify(log.mock.calls)).not.toContain('private ACL detail'); expect(deps.saveCandidate).not.toHaveBeenCalled();
  });

  it('never applies a read grant to PUT, operation status or reconciliation', async () => {
    const deps = dependencies(); const url = await fixture('pack-version', deps);
    const operationUrl = url.replace('pack-version', 'pack-operations/read-request-01');
    for (const response of [
      await fetch(url, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
        sourceVersion: 'draft', sourceCommit: sha, expectedSavedHead: sha, changes: [],
      }) }),
      await fetch(operationUrl),
      await fetch(`${operationUrl}/reconcile`, { method: 'POST' }),
    ]) {
      expect(response.status).toBe(403); expect(response.headers.get('cache-control')).toBe('no-store');
      expect((await response.json()).code).toBe('FORBIDDEN');
    }
    expect(deps.authorizeRead).not.toHaveBeenCalled(); noDataReads(deps);
    expect(deps.saveCandidate).not.toHaveBeenCalled(); expect(deps.reconcileOperation).not.toHaveBeenCalled();
    // Existing operation policy first reads the actor/workflow-scoped receipt, then checks its kind's ACL.
    expect(deps.readOperation).toHaveBeenCalledTimes(2);
    expect(deps.readOperation).toHaveBeenLastCalledWith('read-request-01', 'flow', 'trusted');
  });

  it('does not let a false read policy deny an existing ACL-authorized PUT', async () => {
    const deps = dependencies(); deps.authorizeRead.mockResolvedValue(false); deps.botPermRepo.hasEditPermission.mockResolvedValue(true);
    const url = await fixture('pack-version', deps);
    const response = await fetch(url, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      sourceVersion: 'draft', sourceCommit: sha, expectedSavedHead: sha, changes: [],
    }) });
    expect(response.status).toBe(200); expect(deps.authorizeRead).not.toHaveBeenCalled();
    expect(deps.saveCandidate).toHaveBeenCalledExactlyOnceWith({ actorId: 'trusted', workflowId: 'flow',
      sourceVersion: 'draft', sourceCommit: sha, expectedSavedHead: sha, changes: [] });
  });
});

describe('pack scripts whole-Pack authorization remains required', () => {
  it('allows the unversioned scripts read only after host read and whole-Pack policies', async () => {
    const deps = dependencies(); const url = await fixture('pack-scripts', deps);
    const response = await fetch(`${url}?packId=forged`);
    expect(response.status).toBe(200); expect(await response.json()).toMatchObject(snapshot());
    expect(deps.authorizeRead).toHaveBeenCalledExactlyOnceWith('flow', 'trusted');
    expect(deps.authorizePackRead).toHaveBeenCalledExactlyOnceWith('flow', 'pack', 'trusted');
    expect(deps.authorizePackRead.mock.invocationCallOrder[0]).toBeLessThan(deps.readSnapshot.mock.invocationCallOrder[0]);
    expect(deps.readSnapshot).toHaveBeenCalledExactlyOnceWith('flow', 'pack');
  });

  it.each(['absent', 'denied', 'rejected'])('fails closed before unversioned Git reads when Pack policy is %s', async policy => {
    const deps = dependencies();
    if (policy === 'denied') deps.authorizePackRead.mockResolvedValue(false);
    if (policy === 'rejected') deps.authorizePackRead.mockRejectedValue(new Error('private Pack policy'));
    const url = await fixture('pack-scripts', deps, { authorizePackRead: policy === 'absent' ? undefined : deps.authorizePackRead });
    const response = await fetch(url);
    expect(response.status).toBe(policy === 'denied' ? 403 : 503);
    expect(await response.text()).not.toContain('private Pack policy'); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });

  it.each(['current', 'draft'] as const)('still binds %s Pack authorization to the commit-resolved identity', async version => {
    const deps = dependencies(); deps.readSnapshot.mockResolvedValue({ ...snapshot(version), packId: 'historical-pack' });
    deps.authorizePackRead.mockImplementation(async (_workflowId, packId) => packId !== 'historical-pack');
    const url = await fixture('pack-scripts', deps); const response = await fetch(`${url}?version=${version}`);
    expect(response.status).toBe(403); expect(await response.json()).toEqual(forbidden);
    expect(deps.authorizePackRead).toHaveBeenCalledExactlyOnceWith('flow', 'historical-pack', 'trusted');
    expect(deps.readSnapshot.mock.invocationCallOrder[0]).toBeLessThan(deps.authorizePackRead.mock.invocationCallOrder[0]);
  });
});
