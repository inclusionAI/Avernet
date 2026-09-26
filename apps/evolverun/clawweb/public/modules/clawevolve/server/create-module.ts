import { SkillPackageStorage, type SkillPackageStorageOptions } from "./services/object-storage/skill-package-storage.js";
import { resolveEvolveHostCapabilities } from "./services/evolve/host-capabilities.js";
import type { Router } from "express";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "./repositories/evolve-repository.js";
import { BenchDomainRepository } from "./repositories/bench-domain-repository.js";
import { BenchTemplateRepository } from "./repositories/bench-template-repository.js";
import { BenchRunRepository } from "./repositories/bench-run-repository.js";
import { BenchTemplateVersionRepository } from "./repositories/bench-template-version-repository.js";
import { BenchTaskResultRepository } from "./repositories/bench-task-result-repository.js";
import { BenchArtifactRepository } from "./repositories/bench-artifact-repository.js";
import { BenchTagRepository } from "./repositories/bench-tag-repository.js";
import { AppConfigRepository } from "./repositories/app-config-repository.js";
import { createAppConfigRouter } from "./routes/app-config.js";
import { StageSkillRepository } from "./repositories/stage-skill-repository.js";
import { SkillAssetRepository } from "./repositories/skill-asset-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createEvolveRouter, type EvolveRouterDeps } from "./routes/evolve.js";
import {
  createInternalEvolveRouter,
  type InternalEvolveWorkflowRuntime,
} from "./routes/internal/evolve.js";
import { createInternalTaskGuardRouter } from "./routes/internal/task-guard.js";
import { createBenchRouter } from "./routes/bench.js";
import { createStageSkillsRouter } from "./routes/stage-skills.js";
import { createSkillAssetsRouter } from "./routes/skill-assets.js";
import { dispatchEvolveCommand, cancelEvolveExecution, dispatchEvolveTaskLogArchive, type EvolveDispatchInput } from "./services/evolve-dispatcher.js";
import { configureArtifactBucket, type ObjectStore } from "./services/object-storage/oss-object-store.js";
import { startRunAnalysisTimeoutSweeper } from "./services/evolve/run-analysis-timeout.js";
import { startSuggestionApplyTimeoutSweeper } from "./services/evolve/suggestion-apply-timeout.js";
import { configureClawWebPublicBaseUrl } from "./env.js";
import type { ClawEvolveInternalApi, ClawInsightInternalApi } from "./internal/module-api.js";
import type { BotSkillGateway } from "./contracts/bot-skill-gateway.js";
import type { SpaceDirectory } from "./contracts/space-directory.js";
import { createEvolveSpacesRouter } from "./routes/evolve-spaces.js";
import type { EvolveExtension } from "./contracts/evolve-extension.js";
import { createSkillTaskDefaultsRouter } from "./routes/skill-task-defaults.js";
import { spaceAccessErrorHandler } from "./services/evolve/space-access.js";
import { normalizeEvolveModelConfig, type EvolveModelConfig } from "./model-config.js";

export type ClawevolveModuleOptions = {
  /** Trusted composition-root selection; defaults to internalversion. */
  version?: "openversion" | "internalversion";
  db: IDatabase;
  /** Optional read-only Bot metadata connection for local Singlebox. */
  botDb?: Pick<IDatabase, "query">;
  dispatch?: EvolveRouterDeps["dispatch"];
  /** Host-selected routing; public execution does not infer topology from deployment environment names. */
  resolveExecutionOptions?: (input: Pick<EvolveDispatchInput, "runtime">) =>
    Pick<EvolveDispatchInput, "transport" | "runnerEnvironment">;
  dispatchTaskLogArchive?: EvolveRouterDeps["dispatchTaskLogArchive"];
  cancelExecution?: EvolveRouterDeps["cancelExecution"];
  artifactStore?: ObjectStore;
  artifactUrlStore?: Pick<ObjectStore, "createSignedUrl">;
  /** Signing store whose endpoint is reachable from AIS containers. */
  aisArtifactUrlStore?: Pick<ObjectStore, "createSignedUrl">;
  artifactBucket?: string;
  skillPackageStorage?: SkillPackageStorageOptions;
  clawInsight?: ClawInsightInternalApi;
  insightTaskService?: EvolveRouterDeps["insightTaskService"];
  taskSourceService?: EvolveRouterDeps["taskSourceService"];
  publicBaseUrl?: string;
  trustedPublicOrigins?: readonly string[];
  workflowRuntime?: InternalEvolveWorkflowRuntime;
  hostLocalSkills?: BotSkillGateway;
  hostSpaces?: SpaceDirectory;
  hostExtensions?: readonly EvolveExtension[];
  /** Host-owned model catalog. Public code intentionally defines no provider defaults. */
  modelConfig?: EvolveModelConfig;
};

export type ClawevolveModule = {
  dispatch: NonNullable<EvolveRouterDeps["dispatch"]>;
  publicRouter: Router;
  internalRouter: Router;
  taskGuardRouter: Router;
  benchRouter: Router;
  internalApi: ClawEvolveInternalApi;
  repositories: {
    evolve: EvolveRepository;
    benchDomain: BenchDomainRepository;
    benchTemplate: BenchTemplateRepository;
    benchTemplateVersion: BenchTemplateVersionRepository;
    benchRun: BenchRunRepository;
    benchTaskResult: BenchTaskResultRepository;
  };
  start(): Promise<void>;
  stop(): Promise<void>;
};

/**
 * The single composition root for both embedded ClawWeb and Singlebox modes.
 * Environment-specific transports, evidence, and artifact storage are injected;
 * the domain routes and repositories are identical in both modes.
 */
export function createClawevolveModule(options: ClawevolveModuleOptions): ClawevolveModule {
  const { db } = options;
  if (db.dbType === "noop") throw new Error("Clawevolve requires an available database");
  configureClawWebPublicBaseUrl(options.publicBaseUrl, options.trustedPublicOrigins);
  configureArtifactBucket(options.artifactBucket);

  const skillPackages = new SkillPackageStorage(options.skillPackageStorage, options.artifactStore, options.artifactUrlStore);
  const evolve = new EvolveRepository(db, options.botDb);
  const clawInsight = options.clawInsight ?? null;
  const improvement = clawInsight?.improvementRepository ?? null;
  const benchDomain = new BenchDomainRepository(db);
  const benchTemplate = new BenchTemplateRepository(db);
  const benchRun = new BenchRunRepository(db);
  const benchTemplateVersion = new BenchTemplateVersionRepository(db);
  const benchTaskResult = new BenchTaskResultRepository(db);
  const benchArtifact = new BenchArtifactRepository(db);
  const benchTag = new BenchTagRepository(db);
  const stageSkill = new StageSkillRepository(db);
  const appConfig = new AppConfigRepository(db);
  const skillAsset = new SkillAssetRepository(db);
  const botWorkflowPermission = new BotWorkflowPermissionRepository(db);
  const taskSourceService = options.taskSourceService ?? null;
  const dispatch = options.dispatch ?? ((input) => dispatchEvolveCommand({ ...input, ...options.resolveExecutionOptions?.(input) }, {
    artifactStore: options.artifactStore, artifactUrlStore: options.artifactUrlStore,
  }));
  const insightTaskService = options.insightTaskService ?? null;
  const modelConfig = normalizeEvolveModelConfig(options.modelConfig);

  const capabilities = resolveEvolveHostCapabilities(options);
  const publicRouter = createEvolveRouter(evolve, {
    version: options.version,
    capabilities,
    db,
    dispatch,
    dispatchTaskLogArchive: options.dispatchTaskLogArchive ?? ((input) => dispatchEvolveTaskLogArchive({
      ...input, ...options.resolveExecutionOptions?.(input),
    })),
    cancelExecution: options.cancelExecution ?? ((input) => cancelEvolveExecution({
      ...input, ...options.resolveExecutionOptions?.(input),
    })),
    improvementRepo: improvement,
    taskSourceService,
    insightTaskService,
    benchDomainRepo: benchDomain,
    benchTemplateRepo: benchTemplate,
    benchRunRepo: benchRun,
    artifactStore: options.artifactStore,
    skillPackages,
    artifactUrlStore: options.artifactUrlStore,
    aisArtifactUrlStore: options.aisArtifactUrlStore,
    botWorkflowPermissionRepo: botWorkflowPermission,
    hostLocalSkills: options.hostLocalSkills ?? null,
    hostSpaces: options.hostSpaces,
    hostExtensions: options.hostExtensions,
    stageSkillRepo: stageSkill,
    skillAssetRepo: skillAsset,
    modelConfig,
  });
  publicRouter.use(createStageSkillsRouter({
    repo: stageSkill,
    artifactStore: options.artifactStore,
    skillPackages,
    hostSpaces: options.hostSpaces,
  }));
  publicRouter.use(createSkillAssetsRouter({
    repo: skillAsset,
    hostLocalSkills: options.hostLocalSkills ?? null,
    artifactStore: options.artifactStore,
    skillPackages,
    hostSpaces: options.hostSpaces,
  }));
  publicRouter.use(createEvolveSpacesRouter(options.hostSpaces));
  publicRouter.use("/app-config", createAppConfigRouter(appConfig));
  publicRouter.use(createSkillTaskDefaultsRouter({ skills: skillAsset, stages: stageSkill, config: appConfig,
    spaces: options.hostSpaces, hostExtensions: options.hostExtensions }));
  publicRouter.use(spaceAccessErrorHandler);

  const internalRouter = createInternalEvolveRouter({
    db,
    evolveRepo: evolve,
    workflowRuntime: options.workflowRuntime,
  });
  const taskGuardRouter = createInternalTaskGuardRouter(evolve);
  const benchRouter = createBenchRouter(
    benchDomain,
    benchTemplate,
    benchTemplateVersion,
    benchRun,
    benchTaskResult,
    db,
    benchArtifact,
    benchTag,
  );

  let runAnalysisTimeoutTimer: NodeJS.Timeout | null = null;
  let suggestionApplyTimeoutTimer: NodeJS.Timeout | null = null;

  return {
    dispatch,
    publicRouter,
    internalRouter,
    taskGuardRouter,
    benchRouter,
    internalApi: {
      async createInsightTask(input) {
        if (!insightTaskService) throw new Error("ClawInsight integration is unavailable");
        return insightTaskService.create(input);
      },
    },
    repositories: {
      evolve,
      benchDomain,
      benchTemplate,
      benchTemplateVersion,
      benchRun,
      benchTaskResult,
    },
    async start() {
      if (runAnalysisTimeoutTimer || suggestionApplyTimeoutTimer) return;
      runAnalysisTimeoutTimer = startRunAnalysisTimeoutSweeper(evolve);
      suggestionApplyTimeoutTimer = startSuggestionApplyTimeoutSweeper(evolve);
    },
    async stop() {
      if (runAnalysisTimeoutTimer) clearInterval(runAnalysisTimeoutTimer);
      if (suggestionApplyTimeoutTimer) clearInterval(suggestionApplyTimeoutTimer);
      runAnalysisTimeoutTimer = null;
      suggestionApplyTimeoutTimer = null;
    },
  };
}
