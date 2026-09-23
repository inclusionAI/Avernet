import { describe, expect, it, vi } from 'vitest';
import type { Request } from 'express';
import { createRepairAuthorizer } from '../repair-authorization.js';
const req = { isAdmin: true, headers: { 'x-user-id': 'forged' } } as unknown as Request;
describe('repair authorization at the verified host boundary', () => {
  const permissions = () => ({ hasEditPermission: vi.fn(async () => false), resolveViewScope: vi.fn(async (): Promise<'all' | 'deny' | {botIds: string[]}> => 'all') });
  it('does not trust headers/admin flags without verified login', async () => {
    const access = permissions();
    expect(await createRepairAuthorizer(async () => null, access)(req, 'wf', 'view')).toBeNull();
    expect(access.resolveViewScope).not.toHaveBeenCalled();
  });
  it('permits all-workflow view without edit and rejects mutations', async () => {
    const authorize = createRepairAuthorizer(async () => ({ actorId: 'viewer', isAdmin: false }), permissions());
    expect(await authorize(req, 'wf', 'view')).toEqual({ actorId: 'viewer', canEdit: false });
    expect(await authorize(req, 'wf', 'edit')).toBeNull();
  });
  it('does not expose cross-bot evidence to a limited run viewer', async () => {
    const access = permissions(); access.resolveViewScope.mockResolvedValue({ botIds: ['bot-1'] });
    expect(await createRepairAuthorizer(async () => ({ actorId: 'viewer', isAdmin: false }), access)(req, 'wf', 'view')).toBeNull();
  });
  it('uses verified identity for workflow edit permissions', async () => {
    const access = permissions(); access.hasEditPermission.mockResolvedValue(true);
    expect(await createRepairAuthorizer(async () => ({ actorId: 'editor', isAdmin: false }), access)(req, 'wf', 'edit')).toEqual({ actorId: 'editor', canEdit: true });
    expect(access.hasEditPermission).toHaveBeenCalledWith('wf', 'editor');
  });
  it('only accepts the Host-derived admin role and propagates permission store failure', async () => {
    const access = permissions(); access.hasEditPermission.mockRejectedValue(new Error('DB offline'));
    expect(await createRepairAuthorizer(async () => ({ actorId: 'admin', isAdmin: true }), access)(req, 'wf', 'edit')).toEqual({ actorId: 'admin', canEdit: true });
    await expect(createRepairAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), access)(req, 'wf', 'view')).rejects.toThrow('DB offline');
  });
});
