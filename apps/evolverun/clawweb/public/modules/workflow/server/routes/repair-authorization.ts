import type { Request } from 'express';

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
    const actor = await principal(request);
    if (!actor?.actorId?.trim()) return null;
    const canEdit = actor.isAdmin || await permissions.hasEditPermission(workflowId, actor.actorId);
    if (canEdit) return { actorId: actor.actorId, canEdit: true };
    if (mode === 'edit' || await permissions.resolveViewScope(workflowId, actor.actorId) !== 'all') return null;
    return { actorId: actor.actorId, canEdit: false };
  };
}
