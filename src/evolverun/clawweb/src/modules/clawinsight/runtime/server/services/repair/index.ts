import type { IDatabase, LogAnalysisConfig } from "../../db.js";
import type { EvolveRepository } from "../../repositories/evolve-repository.js";
import { RepairRepository } from "../../repositories/repair-repository.js";
import { AntLogsCollector } from "../antlogs-collector.js";
import type { AistudioService } from "../aistudio-service.js";
import {
  createClawWebOssObjectStore,
  resolveClawWebMistRuntimeScope,
  resolveClawWebOssEnvironment,
} from "../object-storage/clawweb-oss-runtime.js";
import { MistSecretValueProvider } from "../object-storage/mist-credential-provider.js";
import type { MistOssObjectStore } from "../object-storage/oss-object-store.js";
import type { RepairConfig } from "./config.js";
import { RepairLogTool, selectRepairLogSources } from "./log-tool.js";
import { OCB_RESTART_BASE_URLS, OcbRepairGateway } from "./ocb-gateway.js";
import { RepairTaskService, type RepairInsightBridge } from "./repair-runtime.js";
import { RepairRuntimeTool } from "./runtime-tool.js";
import {
  ArcaCommandTransport,
  DirectArcaConnectionProvider,
} from "./arca-command-transport.js";
import { RepositoryRepairTargetResolver } from "./repository-target-resolver.js";

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function createRepairTaskService(
  config: RepairConfig,
  logAnalysisConfig: LogAnalysisConfig,
  options: {
    repo: EvolveRepository;
    db: IDatabase;
    aistudioService: AistudioService;
    objectStore?: MistOssObjectStore;
    insightBridge?: RepairInsightBridge;
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
  const collector = logAnalysisConfig.antlogs ? new AntLogsCollector(logAnalysisConfig.antlogs) : null;
  const repairLogSources = selectRepairLogSources(logAnalysisConfig.antlogs?.sources ?? []);
  const targets = new RepositoryRepairTargetResolver(options.repo);
  const controlPlaneEnvironment = resolveClawWebOssEnvironment();
  const arcaMistScope = resolveClawWebMistRuntimeScope(controlPlaneEnvironment);
  const arcaProxySecret = new MistSecretValueProvider({
    endpoint: process.env.CLAWWEB_MIST_ENDPOINT ?? process.env.INSIGHT_MIST_ENDPOINT ?? "127.0.0.1:11004",
    tenant: process.env.CLAWWEB_MIST_TENANT ?? process.env.INSIGHT_MIST_TENANT ?? "ALIPAY",
    // The local Mist sidecar is initialized once for the ClawWeb control
    // plane. The proxypass secret is granted into that same scope; the
    // target Bot environment only selects the agentclawproxy base URL.
    mode: arcaMistScope.mode,
    appName: arcaMistScope.appName,
    secretName: process.env.REPAIR_ARCA_PROXYPASS_MIST_SECRET_NAME
      ?? "other_manual_agentclawproxy_proxypass_secret",
    timeoutMs: positiveInteger(process.env.CLAWWEB_MIST_TIMEOUT_MS ?? process.env.INSIGHT_MIST_TIMEOUT_MS, 5_000),
    credentialTtlMs: positiveInteger(
      process.env.CLAWWEB_MIST_CREDENTIAL_TTL_MS ?? process.env.INSIGHT_MIST_CREDENTIAL_TTL_MS,
      5 * 60_000,
    ),
  });
  const arcaTransport = new ArcaCommandTransport({
    connectionProvider: new DirectArcaConnectionProvider(arcaProxySecret),
    proxyBaseUrls: {
      pre: process.env.REPAIR_ARCA_PROXY_PRE_BASE_URL,
      prod: process.env.REPAIR_ARCA_PROXY_PROD_BASE_URL,
    },
    tokenTtlSeconds: positiveInteger(process.env.REPAIR_ARCA_PROXYPASS_TOKEN_TTL_SECONDS, 120),
    timeoutSeconds: config.baas.commandTimeoutSeconds,
  });
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
    logs: new RepairLogTool(
      collector,
      repairLogSources.allowedSourceNames,
      repairLogSources.defaultSourceNames,
    ),
    runtimeTool: new RepairRuntimeTool(config.baas, arcaTransport),
    insightBridge: options.insightBridge,
  });
}

export * from "./config.js";
export * from "./contracts.js";
export * from "./errors.js";
export * from "./repair-runtime.js";
export * from "./workload-verifier.js";
