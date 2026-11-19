import type { Request, Response } from 'express';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { requireWorkflowAccess } from '@avernet/clawweb-shared/server/services/workflow-access';

export type WorkflowActionPrincipalResolver = (request: Request) => Promise<{ actorId: string; isAdmin: boolean } | null>;
type WorkflowActionActor = { actorId: string; verifiedAdmin: boolean };

/** Only the workflow-bound suggestion/application routes opt into this policy.
 * The host verifies the login and applies its browser origin/CSRF policy.
 * Embeddings without that verified boundary retain their old edit policy.
 */
export function createEvolveWorkflowActionAccess(
  permissions: BotWorkflowPermissionRepository | null,
  resolveLegacyActor: (request: Request) => string | null,
  resolvePrincipal?: WorkflowActionPrincipalResolver,
) {
  return {
    async actor(request: Request, response: Response): Promise<WorkflowActionActor | null> {
      if (resolvePrincipal && request.method === 'POST'
        && (!request.is('application/json') || request.get('sec-fetch-site') === 'cross-site')) {
        response.status(403).json({ code: 'CSRF_REJECTED', error: 'Forbidden' });
        return null;
      }
      const principal = resolvePrincipal ? await resolvePrincipal(request) : null;
      const actorId = resolvePrincipal ? principal?.actorId?.trim() : resolveLegacyActor(request);
      if (!actorId) { response.status(401).json({ error: 'Unauthorized' }); return null; }
      return { actorId, verifiedAdmin: principal?.isAdmin === true };
    },
    async require(request: Request, response: Response, workflowId: string, actor: WorkflowActionActor, legacyMode: 'view' | 'edit' = 'edit'): Promise<boolean> {
      if (!resolvePrincipal) return requireWorkflowAccess(request, response, permissions, workflowId, legacyMode);
      // In the verified browser policy, neither edit nor execute implies view.
      if (permissions && (actor.verifiedAdmin
        || (await permissions.listBrowserViewableWorkflowIds(actor.actorId, workflowId)).has(workflowId))) return true;
      response.status(403).json({ error: 'Forbidden', message: 'Workflow access required' });
      return false;
    },
  };
}
