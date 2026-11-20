import type { Request } from 'express';
import { recordRepairAccess, repairStage } from '../observability/repair-diagnostics.js';

export type RepairPrincipal = { actorId: string; isAdmin: boolean };
export type RepairPrincipalResolver = (request: Request) => Promise<RepairPrincipal | null>;
export type RepairAuthorize = (request: Request, workflowId: string, mode: 'view' | 'edit', capability?: 'execute') => Promise<{
  actorId: string; canEdit: boolean;
  /** Only a verified workflow grant may opt into manual optimization. */
  canOptimize?: boolean;
  canExecute?: boolean;
} | null>;
type RepairPermissions = {
  hasEditPermission(workflowId: string, actorId: string): Promise<boolean>;
  hasExecutePermission?(workflowId: string, actorId: string): Promise<boolean>;
  hasBrowserPermission?(workflowId: string, actorId: string, field: 'can_edit' | 'can_execute'): Promise<boolean>;
  listBrowserViewableWorkflowIds?(userId: string, workflowId?: string): Promise<Set<string>>;
  resolveViewScope(workflowId: string, actorId: string): Promise<'all' | 'deny' | { botIds: string[] }>;
};

/** Request headers, decoded JWT claims and request.isAdmin are not verified principals.
 * Hosts inject their existing verified login resolver and server-owned role lookup.
 * A batch spans runs. Use the explicit browser workflow grant when provided,
 * not a legacy bot-scoped run viewer grant.
 */
export function createRepairAuthorizer(principal: RepairPrincipalResolver, permissions: RepairPermissions): RepairAuthorize {
  return async (request, workflowId, mode, capability) => {
    const actor = await repairStage('principal', () => principal(request));
    const access = { workflowId, mode, actorId: actor?.actorId ?? null };
    if (!actor?.actorId?.trim()) { recordRepairAccess({ ...access, reason: 'MISSING_IDENTITY' }); return null; }
    const canEdit = actor.isAdmin || await repairStage('permissions', () => permissions.hasBrowserPermission?.(workflowId, actor.actorId, 'can_edit')
      ?? permissions.hasEditPermission(workflowId, actor.actorId));
    if (!canEdit && mode === 'edit') { recordRepairAccess({ ...access, reason: 'INSUFFICIENT_EDIT_PERMISSION' }); return null; }
    const scope = actor.isAdmin || mode === 'edit' ? 'all' : await repairStage('permissions', async () => {
      if (permissions.listBrowserViewableWorkflowIds) {
        return (await permissions.listBrowserViewableWorkflowIds(actor.actorId, workflowId)).has(workflowId) ? 'all' : 'deny';
      }
      return canEdit ? 'all' : permissions.resolveViewScope(workflowId, actor.actorId);
    });
    if (scope !== 'all') {
      recordRepairAccess({ ...access, reason: 'INSUFFICIENT_VIEW_SCOPE', viewScope: scope === 'deny' ? 'deny' : 'limited' });
      return null;
    }
    recordRepairAccess({ ...access, reason: 'ALLOWED', viewScope: 'all' });
    const canExecute = capability === 'execute' && await repairStage('execute_permissions', () =>
      permissions.hasBrowserPermission?.(workflowId, actor.actorId, 'can_execute')
        ?? permissions.hasExecutePermission?.(workflowId, actor.actorId) ?? Promise.resolve(false));
    return { actorId: actor.actorId, canEdit, canOptimize: true, ...(capability === 'execute' ? { canExecute } : {}) };
  };
}
