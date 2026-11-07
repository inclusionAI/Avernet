import type { RepairPrincipalResolver } from './repair-authorization.js';
import type { RepairAuthorize } from './repair-batches.js';

type SubworkflowPermissions = {
  resolveViewScope(workflowId: string, actorId: string): Promise<'all' | 'deny' | { botIds: string[] }>;
};

/** Subworkflow observations span runs and require verified whole-workflow read access. */
export function createSubworkflowAuthorizer(
  principal: RepairPrincipalResolver,
  permissions: SubworkflowPermissions,
): RepairAuthorize {
  return async (request, workflowId, mode) => {
    if (mode !== 'view') return null;
    const actor = await principal(request);
    if (!actor?.actorId?.trim()) return null;
    if (!actor.isAdmin && await permissions.resolveViewScope(workflowId, actor.actorId) !== 'all') return null;
    return { actorId: actor.actorId, canEdit: false };
  };
}
