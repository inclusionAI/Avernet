import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { InsightManagementRepository } from "../../../repositories/insight-management-repository.js";
import { createInsightManagementRouter } from "../../../routes/insight-management.js";
import { InsightAgentAuthorizer, resolveInsightAgentClients } from "../agent-auth.js";
import { InsightManagementService } from "./service.js";

/** Explicit host composition. Machine clients require a configured signed identity, even on localhost. */
export function createInsightManagementRuntime(db: IDatabase, env: Record<string, string | undefined> = process.env) {
  const service = new InsightManagementService(new InsightManagementRepository(db));
  const authorizer = new InsightAgentAuthorizer({ clients: resolveInsightAgentClients(env), allowLocalUnsigned: false });
  return { service, router: createInsightManagementRouter(service, authorizer) };
}
