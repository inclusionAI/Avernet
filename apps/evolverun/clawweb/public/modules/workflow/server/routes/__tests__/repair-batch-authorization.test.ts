import { describe, expect, it, vi } from 'vitest';
import type { Request } from 'express';
import { createRepairAuthorizer } from '../repair-authorization.js';

const req = { isAdmin: true, headers: { 'x-user-id': 'forged' } } as unknown as Request;
const permissions = () => ({
  hasEditPermission: vi.fn(async () => false),
  hasExecutePermission: vi.fn(async () => false),
  resolveViewScope: vi.fn(async (): Promise<'all' | 'deny' | { botIds: string[] }> => 'all'),
});
const viewer = async () => ({ actorId: 'viewer', isAdmin: false });

describe('repair authorization at the verified host boundary', () => {
  it('does not trust headers/admin flags without verified login', async () => {
    const access = permissions();
    expect(await createRepairAuthorizer(async () => null, access)(req, 'wf', 'view')).toBeNull();
    expect(access.resolveViewScope).not.toHaveBeenCalled();
  });
  it('permits whole-workflow view and manual optimization without granting edit', async () => {
    const authorize = createRepairAuthorizer(viewer, permissions());
    expect(await authorize(req, 'wf', 'view')).toEqual({ actorId: 'viewer', canEdit: false, canOptimize: true });
    expect(await authorize(req, 'wf', 'edit')).toBeNull();
  });
  it('does not mistake a legacy bot-scoped run grant for a workflow grant', async () => {
    const access = permissions(); access.resolveViewScope.mockResolvedValue({ botIds: ['bot-1'] });
    expect(await createRepairAuthorizer(viewer, access)(req, 'wf', 'view')).toBeNull();
  });
  it('checks the exact explicit browser grant without falling back to legacy run scope', async () => {
    const access = { ...permissions(), listBrowserViewableWorkflowIds: vi.fn(async () => new Set(['wf'])) };
    const authorize = createRepairAuthorizer(viewer, access);
    expect(await authorize(req, 'wf', 'view')).toMatchObject({ canEdit: false, canOptimize: true });
    expect(access.listBrowserViewableWorkflowIds).toHaveBeenCalledWith('viewer', 'wf');
    expect(await authorize(req, 'wf-other', 'view')).toBeNull();
    expect(access.resolveViewScope).not.toHaveBeenCalled();
    access.hasEditPermission.mockResolvedValue(true);
    expect(await authorize(req, 'wf-other', 'view')).toBeNull();
    expect(await authorize(req, 'wf-other', 'edit')).toMatchObject({ canEdit: true });
  });
  it('uses verified identity for workflow edit permissions', async () => {
    const access = permissions(); access.hasEditPermission.mockResolvedValue(true);
    expect(await createRepairAuthorizer(async () => ({ actorId: 'editor', isAdmin: false }), access)(req, 'wf', 'edit'))
      .toMatchObject({ actorId: 'editor', canEdit: true });
    expect(access.hasEditPermission).toHaveBeenCalledWith('wf', 'editor');
  });
  it('only accepts the Host-derived admin role and propagates permission store failure', async () => {
    const access = permissions(); access.hasEditPermission.mockRejectedValue(new Error('DB offline'));
    expect(await createRepairAuthorizer(async () => ({ actorId: 'admin', isAdmin: true }), access)(req, 'wf', 'edit'))
      .toMatchObject({ actorId: 'admin', canEdit: true });
    await expect(createRepairAuthorizer(viewer, access)(req, 'wf', 'view')).rejects.toThrow('DB offline');
  });
  it('requires an independent execute grant, including for verified administrators', async () => {
    const access = permissions();
    const authorize = createRepairAuthorizer(viewer, access);
    const admin = createRepairAuthorizer(async () => ({ actorId: 'admin', isAdmin: true }), access);
    expect(await authorize(req, 'wf', 'view', 'execute')).toMatchObject({ actorId: 'viewer', canEdit: false, canExecute: false });
    expect(await admin(req, 'wf', 'view', 'execute')).toMatchObject({ canEdit: true, canExecute: false });
    access.hasExecutePermission.mockResolvedValue(true);
    expect(await authorize(req, 'wf', 'view', 'execute')).toMatchObject({ actorId: 'viewer', canEdit: false, canExecute: true });
    expect(access.hasExecutePermission).toHaveBeenLastCalledWith('wf', 'viewer');
  });
});
