/** Stateless composition: host owns identity/ACL; BaaS owns message state. */
import type { Request, Router } from 'express';
import { createHmac } from 'node:crypto';
import { getCurrentEnv } from '@avernet/clawweb-shared/server/env';
import { resolveBaasConfig, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createWorkflowAiDraftReceipts } from '@avernet/workflow/server/services/workflow-ai-draft-receipts';
import { createWorkflowAiDraftService } from '@avernet/workflow/server/services/workflow-ai-draft-service';
import { createWorkflowAiDraftRouter } from '@avernet/workflow/server/routes/workflow-ai-drafts';
import type { WorkflowAiDraftExecutionPort, WorkflowAiDraftService, WorkflowAiDraftReceipts } from '@avernet/workflow/server/contracts/workflow-ai-draft';

export function createWorkflowAiDraftRuntime(db: IDatabase, options: {
  principal(request: Request): Promise<{ actorId: string } | null>;
  publicOrigin?: string;
  publicOrigins?: readonly string[];
  canEdit?(workflowId: string, actorId: string): Promise<boolean>;
  execution?: WorkflowAiDraftExecutionPort; unavailableReason?: string;
  /** Trusted server-only codec; defaults to a domain-separated BaaS-key signer. */
  receipts?: WorkflowAiDraftReceipts;
}): { service: WorkflowAiDraftService; router: Router; monitor: { reconcile(limit?: number): Promise<number> } } {
  const permissions = new BotWorkflowPermissionRepository(db);
  const configuredReceipts = () => {
    const transport = resolveBaasConfig();
    const endpoint = new URL(transport.baseUrl);
    if (!transport.apiKey || !['https:', 'http:'].includes(endpoint.protocol)
      || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new Error('AI_DRAFT_RECOVERY_UNAVAILABLE');
    // Re-evaluate the trusted namespace on every issue/verify, so a live
    // credential/provider change revokes old receipts before provider I/O.
    const key = createHmac('sha256', transport.apiKey)
      .update(JSON.stringify(['workflow-ai-draft-receipts/v1', getCurrentEnv(), endpoint.href.replace(/\/$/, '')]))
      .digest('base64url');
    return createWorkflowAiDraftReceipts(key);
  };
  let receipts = options.receipts;
  if (!receipts) {
    try {
      configuredReceipts();
      receipts = {
        issue: claims => configuredReceipts().issue(claims),
        verify: token => configuredReceipts().verify(token),
      };
    } catch { /* Missing transport cannot prepare a recoverable request. */ }
  }
  const service = createWorkflowAiDraftService({ receipts,
    canEdit: options.canEdit ?? ((workflowId, actorId) => permissions.hasEditPermission(workflowId, actorId)),
    execution: options.execution, unavailableReason: options.unavailableReason });
  return { service, router: createWorkflowAiDraftRouter({ service, principal: options.principal, publicOrigin: options.publicOrigin, publicOrigins: options.publicOrigins }),
    monitor: { reconcile: limit => service.reconcile(limit) } };
}
