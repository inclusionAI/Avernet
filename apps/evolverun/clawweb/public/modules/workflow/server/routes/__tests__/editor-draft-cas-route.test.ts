// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { createEditorDraftCasRouter } from '../editor-draft-cas.js';

const draft = { workflowId: 'flow', packId: 'pack', specJson: '{"id":"flow","nodes":[]}', draftToken: 'a'.repeat(64) };
let server: Server, base: string;
const resolvePrincipal = vi.fn(), read = vi.fn(), save = vi.fn();
beforeEach(async () => {
  resolvePrincipal.mockReset().mockResolvedValue({ actorId: 'trusted' });
  read.mockReset().mockResolvedValue(draft); save.mockReset().mockResolvedValue(draft);
  const app = express(); app.use(express.json());
  app.use('/api/workflows', createEditorDraftCasRouter({ resolvePrincipal, read, save }));
  server = await new Promise<Server>(resolve => { const value = app.listen(0, '127.0.0.1', () => resolve(value)); });
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('missing address');
  base = `http://127.0.0.1:${address.port}/api/workflows/flow/editor-draft`;
});
afterEach(async () => { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); });
const put = (body: unknown) => fetch(base, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

describe('editor draft CAS route contract', () => {
  it('reads raw draft and token using only trusted principal with no-store', async () => {
    const response = await fetch(base);
    expect(response.status).toBe(200); expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual(draft); expect(read).toHaveBeenCalledWith('flow', 'trusted');
  });
  it('saves with required token and server principal and no fallback', async () => {
    const spec = { id: 'flow', title: 'Draft', nodes: [] };
    const response = await put({ expectedDraftToken: draft.draftToken, spec });
    expect(response.status).toBe(200); expect(await response.json()).toEqual(draft);
    expect(save).toHaveBeenCalledWith({ workflowId: 'flow', actorId: 'trusted', expectedDraftToken: draft.draftToken, spec });
  });
  it('rejects unauthenticated requests without reading or writing', async () => {
    resolvePrincipal.mockResolvedValue(null);
    expect((await fetch(base)).status).toBe(401);
    expect((await put({ expectedDraftToken: draft.draftToken, spec: {} })).status).toBe(401);
    expect(read).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled();
  });
  it.each([
    { spec: {} }, { expectedDraftToken: 'bad', spec: {} },
    { expectedDraftToken: draft.draftToken, spec: {}, actorId: 'forged' },
    { expectedDraftToken: draft.draftToken, spec: {}, packId: null },
    { expectedDraftToken: draft.draftToken, spec: {}, packId: '../other' },
    { expectedDraftToken: draft.draftToken, spec: [], },
  ])('rejects malformed request %# without a write', async body => {
    expect((await put(body)).status).toBe(400); expect(save).not.toHaveBeenCalled();
  });
  it('rejects query arguments and unsupported methods', async () => {
    expect((await fetch(`${base}?actorId=other`)).status).toBe(400);
    expect((await fetch(base, { method: 'DELETE' })).status).toBe(405);
    expect(read).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled();
  });
  it('reports an unresolved legacy operation without returning its details', async () => {
    save.mockRejectedValueOnce(Object.assign(new Error('private details'), { status: 409, code: 'LEGACY_PACK_OPERATION_UNRESOLVED' }));
    const response = await put({ expectedDraftToken: draft.draftToken, spec: {} });
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ code: 'LEGACY_PACK_OPERATION_UNRESOLVED' });
  });
  it('returns only a known safe conflict code and never forwards private errors', async () => {
    save.mockRejectedValueOnce(Object.assign(new Error('secret information'), { status: 409, code: 'DRAFT_CONFLICT' }));
    let response = await put({ expectedDraftToken: draft.draftToken, spec: {} });
    expect(response.status).toBe(409); expect(await response.json()).toEqual({ code: 'DRAFT_CONFLICT' });
    read.mockRejectedValueOnce(new Error('secret information'));
    response = await fetch(base); expect(response.status).toBe(503);
    expect(JSON.stringify(await response.json())).not.toContain('secret information');
  });
});
