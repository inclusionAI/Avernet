// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPackScriptsRouter } from '../pack-scripts.js';

const snapshot = { workflowId: 'wf-1', packId: 'pack-1', baseCommit: 'abc', baseRef: 'origin/wf/wf-1', files: [{ path: 'scripts/a.py', content: 'print(1)', mode: '100644' as const }] };
function dependencies() {
  return {
    resolvePrincipal: vi.fn(async (_req: express.Request): Promise<{ actorId: string } | null> => ({ actorId: 'trusted' })),
    botPermRepo: { hasEditPermission: vi.fn(async (_workflowId: string, _actorId: string) => true) },
    workflowSpecRepo: { findByWorkflowId: vi.fn(async (_id: string): Promise<{ workflow_id: string; pack_id: string | null } | null> => ({ workflow_id: 'wf-1', pack_id: 'pack-1' })) },
    authorizePackRead: vi.fn(async (_workflowId: string, _packId: string, _actorId: string) => true),
    readSnapshot: vi.fn(async (_workflowId: string, _packId: string) => snapshot),
  };
}
let server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => { if (server) await new Promise<void>((resolve, reject) => server!.close(err => err ? reject(err) : resolve())); server = undefined; });
async function request(deps: Parameters<typeof createPackScriptsRouter>[0], path = 'wf-1', headers: Record<string, string> = {}) {
  const app = express(); app.use('/api/workflows', createPackScriptsRouter(deps));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('No address');
  return fetch(`http://127.0.0.1:${address.port}/api/workflows/${path}/pack-scripts?packId=forged`, { headers });
}
describe('pack scripts read route', () => {
  it('does not expose a disabled host before identity or Git access', async () => {
    const deps = dependencies(); const res = await request({ ...deps, enabled: false });
    expect(res.status).toBe(503); expect(await res.json()).toEqual({ error: 'Pack scripts unavailable', code: 'SERVICE_UNAVAILABLE' }); expect(deps.resolvePrincipal).not.toHaveBeenCalled();
    expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('rejects anonymous requests and forged identity headers before permission or Git', async () => {
    const deps = dependencies(); deps.resolvePrincipal.mockResolvedValue(null);
    const res = await request(deps, 'wf-1', { 'x-user-id': 'admin', cookie: 'user=admin' });
    expect(res.status).toBe(401); expect(await res.json()).toEqual({ error: 'Authentication required', code: 'AUTHENTICATION_REQUIRED' }); expect(res.headers.get('cache-control')).toBe('no-store');
    expect(deps.botPermRepo.hasEditPermission).not.toHaveBeenCalled(); expect(deps.workflowSpecRepo.findByWorkflowId).not.toHaveBeenCalled(); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('requires explicit authorization before DB and Git', async () => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockResolvedValue(false);
    const res = await request(deps); expect(res.status).toBe(403); expect(await res.json()).toEqual({ error: 'Forbidden', code: 'FORBIDDEN' });
    expect(deps.botPermRepo.hasEditPermission).toHaveBeenCalledWith('wf-1', 'trusted');
    expect(deps.workflowSpecRepo.findByWorkflowId).not.toHaveBeenCalled(); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('fails closed when permission service is missing', async () => {
    const deps = dependencies(); const res = await request({ ...deps, botPermRepo: null });
    expect(res.status).toBe(403); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('returns 404 for missing row without reading Git', async () => {
    const deps = dependencies(); deps.workflowSpecRepo.findByWorkflowId.mockResolvedValue(null);
    const res = await request(deps); expect(res.status).toBe(404); expect(await res.json()).toEqual({ error: 'Workflow not found', code: 'WORKFLOW_NOT_FOUND' }); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('fails closed without authoritative Pack access, even when workflow permission passes', async () => {
    const deps = dependencies(); const res = await request({ ...deps, authorizePackRead: undefined });
    expect(res.status).toBe(503); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('does not read Git if authoritative Pack access is denied', async () => {
    const deps = dependencies(); deps.authorizePackRead.mockResolvedValue(false);
    const res = await request(deps); expect(res.status).toBe(403);
    expect(deps.authorizePackRead).toHaveBeenCalledWith('wf-1', 'pack-1', 'trusted');
    expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('returns the snapshot using the database pack ID, not query input', async () => {
    const deps = dependencies(); const res = await request(deps);
    expect(res.status).toBe(200); expect(res.headers.get('cache-control')).toBe('no-store'); expect(await res.json()).toEqual(snapshot);
    expect(deps.readSnapshot).toHaveBeenCalledWith('wf-1', 'pack-1');
    expect(deps.resolvePrincipal.mock.invocationCallOrder[0]).toBeLessThan(deps.botPermRepo.hasEditPermission.mock.invocationCallOrder[0]);
    expect(deps.botPermRepo.hasEditPermission.mock.invocationCallOrder[0]).toBeLessThan(deps.workflowSpecRepo.findByWorkflowId.mock.invocationCallOrder[0]);
    expect(deps.workflowSpecRepo.findByWorkflowId.mock.invocationCallOrder[0]).toBeLessThan(deps.readSnapshot.mock.invocationCallOrder[0]);
  });
  it('falls back to the database workflow ID when pack ID is null', async () => {
    const deps = dependencies(); deps.workflowSpecRepo.findByWorkflowId.mockResolvedValue({ workflow_id: 'wf-1', pack_id: null });
    expect((await request(deps)).status).toBe(200); expect(deps.readSnapshot).toHaveBeenCalledWith('wf-1', 'wf-1');
  });
  it('rejects invalid path identifiers before Git', async () => {
    const deps = dependencies(); expect((await request(deps, 'bad..id')).status).toBe(400); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('rejects invalid database identifiers before Git', async () => {
    const deps = dependencies(); deps.workflowSpecRepo.findByWorkflowId.mockResolvedValue({ workflow_id: 'wf-1', pack_id: '../secret' });
    expect((await request(deps)).status).toBe(503); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it('sanitizes resolver failures before authorization', async () => {
    const deps = dependencies(); deps.resolvePrincipal.mockRejectedValue(new Error('private identity detail'));
    const res = await request(deps); expect(res.status).toBe(503); expect(await res.text()).not.toContain('private identity detail');
    expect(deps.botPermRepo.hasEditPermission).not.toHaveBeenCalled(); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
  it.each([['Workflow ref conflict: histories diverged', 409, 'SNAPSHOT_CONFLICT'], ['Pack manifest conflict', 409, 'SNAPSHOT_CONFLICT'], ['Shared pack requires separate authorization', 409, 'SNAPSHOT_CONFLICT'], ['Workflow snapshot not found', 404, 'SNAPSHOT_NOT_FOUND'], ['Pack manifest not found', 404, 'SNAPSHOT_NOT_FOUND'], ['fatal: secret /internal/repository script contents', 503, 'SERVICE_UNAVAILABLE']])('sanitizes snapshot error %s', async (message, status, code) => {
    const deps = dependencies(); deps.readSnapshot.mockRejectedValue(new Error(message));
    const log = vi.spyOn(console, 'error'); try {
      const res = await request(deps); expect(res.status).toBe(status); expect(res.headers.get('cache-control')).toBe('no-store');
      const body = await res.text(); expect(JSON.parse(body).code).toBe(code); expect(body).not.toContain(message); expect(body).not.toContain('/internal/repository'); expect(body).not.toContain('script contents');
      expect(log).not.toHaveBeenCalled();
    } finally { log.mockRestore(); }
  });
  it('sanitizes permission and principal failures without invoking Git', async () => {
    const deps = dependencies(); deps.botPermRepo.hasEditPermission.mockRejectedValue(new Error('private DB path'));
    const res = await request(deps); expect(res.status).toBe(503); expect(await res.text()).not.toContain('private DB path'); expect(deps.readSnapshot).not.toHaveBeenCalled();
  });
});
