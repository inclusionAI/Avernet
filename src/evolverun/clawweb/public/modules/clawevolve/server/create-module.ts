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
import { BotWorkflowPermissionRepository } from "./repositories/bot-workflow-permission-repository.js";
import { createEvolveRouter, type EvolveRouterDeps } from "./routes/evolve.js";
import {
  createInternalEvolveRouter,
  type InternalEvolveWorkflowRuntime,
} from "./routes/internal/evolve.js";
import { createInternalTaskGuardRouter } from "./routes/internal/task-guard.js";
import { createBenchRouter } from "./routes/bench.js";
import { dispatchEvolveCommand } from "./services/evolve-dispatcher.js";
import { configureArtifactBucket, type ObjectStore } from "./services/object-storage/oss-object-store.js";
import { startRunAnalysisTimeoutSweeper } from "./services/evolve/run-analysis-timeout.js";
import { startSuggestionApplyTimeoutSweeper } from "./services/evolve/suggestion-apply-timeout.js";
import { configureClawWebPublicBaseUrl } from "./env.js";
import type { ClawEvolveInternalApi, ClawInsightInternalApi } from "./internal/module-api.js";

export type ClawevolveModuleOptions = {
  db: IDatabase;
  dispatch?: EvolveRouterDeps["dispatch"];
  dispatchTaskLogArchive?: EvolveRouterDeps["dispatchTaskLogArchive"];
  cancelExecution?: EvolveRouterDeps["cancelExecution"];
  artifactStore?: ObjectStore;
  artifactUrlStore?: Pick<ObjectStore, "createSignedUrl">;
  artifactBucket?: string;
  clawInsight?: ClawInsightInternalApi;
  insightTaskService?: EvolveRouterDeps["insightTaskService"];
  taskSourceService?: EvolveRouterDeps["taskSourceService"];
  publicBaseUrl?: string;
  trustedPublicOrigins?: readonly string[];
  workflowRuntime?: InternalEvolveWorkflowRuntime;
};

export type ClawevolveModule = {
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

  const evolve = new EvolveRepository(db);
  const clawInsight = options.clawInsight ?? null;
  const improvement = clawInsight?.improvementRepository ?? null;
  const benchDomain = new BenchDomainRepository(db);
  const benchTemplate = new BenchTemplateRepository(db);
  const benchRun = new BenchRunRepository(db);
  const benchTemplateVersion = new BenchTemplateVersionRepository(db);
  const benchTaskResult = new BenchTaskResultRepository(db);
  const benchArtifact = new BenchArtifactRepository(db);
  const benchTag = new BenchTagRepository(db);
  const botWorkflowPermission = new BotWorkflowPermissionRepository(db);
  const taskSourceService = options.taskSourceService ?? null;
  const dispatch = options.dispatch ?? dispatchEvolveCommand;
  const insightTaskService = options.insightTaskService ?? null;

  const publicRouter = createEvolveRouter(evolve, {
    db,
    dispatch,
    dispatchTaskLogArchive: options.dispatchTaskLogArchive,
    cancelExecution: options.cancelExecution,
    improvementRepo: improvement,
    taskSourceService,
    insightTaskService,
    benchDomainRepo: benchDomain,
    benchTemplateRepo: benchTemplate,
    benchRunRepo: benchRun,
    artifactStore: options.artifactStore,
    artifactUrlStore: options.artifactUrlStore,
    botWorkflowPermissionRepo: botWorkflowPermission,
  });

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
