import type { BotRuntime, BotSessionTrajectoryReader } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { IDatabase, LogAnalysisConfig } from "@avernet/clawweb-shared/server/db";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { RepairRepository } from "../../../repositories/repair/repair-repository.js";
import type { AistudioService } from "../../aistudio-service.js";
import {
  createClawWebOssObjectStore,
  resolveClawWebOssEnvironment,
} from "../../object-storage/clawweb-oss-runtime.js";
import type { MistOssObjectStore } from "../../object-storage/oss-object-store.js";
import type { RepairConfig } from "./config.js";
import { createAgentTools } from "../common/agent-tools/index.js";
import { OCB_RESTART_BASE_URLS, OcbRepairGateway } from "./ocb-gateway.js";
import { RepairTaskService, type RepairInsightBridge } from "./repair-runtime.js";
import { RepositoryRepairTargetResolver } from "./repository-target-resolver.js";

export function createRepairTaskService(
  config: RepairConfig,
  logAnalysisConfig: LogAnalysisConfig,
  options: {
    runtime: Pick<BotRuntime, "executeShell"> & Partial<Pick<BotRuntime, "request">>;
    repo: EvolveRepository;
    db: IDatabase;
    aistudioService: AistudioService;
    objectStore?: MistOssObjectStore;
    insightBridge?: RepairInsightBridge;
    tools?: ReturnType<typeof createAgentTools>;
  },
): RepairTaskService {
  // Repair artifacts are uploaded by AIStudio. Keep this feature-scoped and
  // use the same V1 signing contract already verified by Session Analysis in
  // that runtime; the shared object-store default remains V4.
  const store = options.objectStore ?? createClawWebOssObjectStore(
    resolveClawWebOssEnvironment(),
    process.env,
    { signedUrlVersion: "v1" },
  );
  const tools = options.tools ?? createAgentTools(logAnalysisConfig, options.runtime);
  const targets = new RepositoryRepairTargetResolver(options.repo);

  return new RepairTaskService({
    config,
    repo: options.repo,
    repairRepo: new RepairRepository(options.db),
    store,
    ais: options.aistudioService,
    targets,
    ocb: new OcbRepairGateway({
      baseUrls: OCB_RESTART_BASE_URLS,
      timeoutMs: config.requestTimeoutMs,
    }),
    ...tools,
    insightBridge: options.insightBridge,
  });
}

export * from "./config.js";
export * from "./contracts.js";
export * from "../common/agent-tools/errors.js";
export * from "./repair-runtime.js";
export * from "./workload-verifier.js";
