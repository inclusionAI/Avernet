import type { Request } from 'express';
import { recordRepairAccess, repairStage } from '../observability/repair-diagnostics.js';

export type RepairPrincipal = { actorId: string; isAdmin: boolean };
export type RepairPrincipalResolver = (request: Request) => Promise<RepairPrincipal | null>;
type RepairPermissions = {
  hasEditPermission(workflowId: string, actorId: string): Promise<boolean>;
  resolveViewScope(workflowId: string, actorId: string): Promise<'all' | 'deny' | { botIds: string[] }>;
};

/** Request headers, decoded JWT claims and request.isAdmin are not verified principals.
 * Hosts inject their existing verified login resolver and server-owned role lookup.
 * A batch can span bots/runs, so a limited run viewer cannot read a whole-workflow batch.
 */
export function createRepairAuthorizer(principal: RepairPrincipalResolver, permissions: RepairPermissions) {
  return async (request: Request, workflowId: string, mode: 'view' | 'edit') => {
    const actor = await repairStage('principal', () => principal(request));
    const access = { workflowId, mode, actorId: actor?.actorId ?? null };
    if (!actor?.actorId?.trim()) { recordRepairAccess({ ...access, reason: 'MISSING_IDENTITY' }); return null; }
    const canEdit = actor.isAdmin || await repairStage('permissions', () => permissions.hasEditPermission(workflowId, actor.actorId));
    if (canEdit) { recordRepairAccess({ ...access, reason: 'ALLOWED', viewScope: 'all' }); return { actorId: actor.actorId, canEdit: true }; }
    if (mode === 'edit') { recordRepairAccess({ ...access, reason: 'INSUFFICIENT_EDIT_PERMISSION' }); return null; }
    const scope = await repairStage('permissions', () => permissions.resolveViewScope(workflowId, actor.actorId));
    if (scope !== 'all') {
      recordRepairAccess({ ...access, reason: 'INSUFFICIENT_VIEW_SCOPE', viewScope: scope === 'deny' ? 'deny' : 'limited' });
      return null;
    }
    recordRepairAccess({ ...access, reason: 'ALLOWED', viewScope: 'all' });
    return { actorId: actor.actorId, canEdit: false };
  };
}
