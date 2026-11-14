/** Reusable composition root; host owns verified identity, timer and shutdown. */
import type { Request, Router } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createWorkflowAiDraftService } from '@avernet/workflow/server/services/workflow-ai-draft-service';
import { createWorkflowAiDraftRouter } from '@avernet/workflow/server/routes/workflow-ai-drafts';
import type { WorkflowAiDraftExecutionPort, WorkflowAiDraftService } from '@avernet/workflow/server/contracts/workflow-ai-draft';

export function createWorkflowAiDraftRuntime(db: IDatabase, options: {
  principal(request: Request): Promise<{ actorId: string } | null>;
  publicOrigin?: string;
  publicOrigins?: readonly string[];
  canEdit?(workflowId: string, actorId: string): Promise<boolean>;
  execution?: WorkflowAiDraftExecutionPort; unavailableReason?: string;
}): { service: WorkflowAiDraftService; router: Router; monitor: { reconcile(limit?: number): Promise<number> } } {
  const permissions = new BotWorkflowPermissionRepository(db);
  const service = createWorkflowAiDraftService({ db,
    canEdit: options.canEdit ?? ((workflowId, actorId) => permissions.hasEditPermission(workflowId, actorId)),
    execution: options.execution, unavailableReason: options.unavailableReason });
  return { service, router: createWorkflowAiDraftRouter({ service, principal: options.principal, publicOrigin: options.publicOrigin, publicOrigins: options.publicOrigins }),
    monitor: { reconcile: limit => service.reconcile(limit) } };
}
