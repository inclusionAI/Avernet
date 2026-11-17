import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { createAIStudioServiceAuthenticator } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
import { AIStudioTrainWatchRepository } from '@avernet/workflow/server/repositories/aistudio-train-watch-repository';
import { createAIStudioTrainRouter, type AIStudioTrainPublicAuth } from '@avernet/workflow/server/routes/aistudio-train';
import { createInternalAIStudioTrainWatchesRouter } from '@avernet/workflow/server/routes/internal/aistudio-train-watches';
import { AIStudioTrainManualAuditRepository } from '@avernet/workflow/server/repositories/aistudio-train-manual-audit-repository';
import { createInternalAIStudioTrainManualAuditRouter } from '@avernet/workflow/server/routes/internal/events';
export { captureAIStudioRawBody } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
export type { AIStudioTrainPublicAuth } from '@avernet/workflow/server/routes/aistudio-train';

/** Composition only: host supplies real IAM/ACL callbacks; never derives a
 * principal from X-User-Id, decoded cookies or legacy admin flags. Missing host
 * providers disable public access (503). Internal writes always require the
 * configured Ed25519 service key, even when running in a dev profile. */
export function createAIStudioTrainRuntime(db: IDatabase, options: AIStudioTrainPublicAuth & {
  publicKeyB64: string; publicOrigin?: string;
}) {
  const store = new AIStudioTrainWatchRepository(db);
  const resolveVerifiedService = createAIStudioServiceAuthenticator({publicKeyB64:options.publicKeyB64});
  return {
    internalRouter: createInternalAIStudioTrainWatchesRouter({
      store, findRunTarget:flowId => store.findRunTarget(flowId),
      resolveVerifiedService,
    }),
    auditRouter: createInternalAIStudioTrainManualAuditRouter({
      repository:new AIStudioTrainManualAuditRepository(db),resolveVerifiedService,
      findRunTarget:flowId => store.findRunTarget(flowId),findWatch:(flowId,nodeId,attempt) => store.find(flowId,nodeId,attempt),
    }),
    publicRouter: createAIStudioTrainRouter({
      ...options,store,findRunTarget:flowId => store.findRunTarget(flowId),
      findNodeTarget:(flowId,nodeId) => store.findNodeTarget(flowId,nodeId),
    }),
  };
}
