import type { Router } from "express";
import type { IDatabase } from "./db.js";
import { EvolveRepository } from "./repositories/evolve-repository.js";
import { EvolveTaskSourceRepository } from "./repositories/evolve-task-source-repository.js";
import { BenchDomainRepository } from "./repositories/bench-domain-repository.js";
import { BenchTemplateRepository } from "./repositories/bench-template-repository.js";
import { BenchRunRepository } from "./repositories/bench-run-repository.js";
import { BotWorkflowPermissionRepository } from "./repositories/bot-workflow-permission-repository.js";
import { FlowRunRepository } from "./repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "./repositories/node-execution-repository.js";
import { NodeStepTraceRepository } from "./repositories/node-step-traces-repository.js";
import { RunLogRepository } from "./repositories/run-log-repository.js";
import { ExecutionStepLogRepository } from "./repositories/execution-step-log-repository.js";
import { createEvolveRouter, type EvolveRouterDeps } from "./routes/evolve.js";
import { createInternalEvolveRouter } from "./routes/internal/evolve.js";
import { createInternalTaskGuardRouter } from "./routes/internal/task-guard.js";
import { TaskSourceService } from "./services/evolve/task-source-service.js";
import { dispatchEvolveCommand } from "./services/evolve-dispatcher.js";
import { InsightPlanStepService } from "./services/evolve/insight-plan-step-service.js";
import { InsightTaskService } from "./services/evolve/insight-task-service.js";
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
  publicBaseUrl?: string;
  trustedPublicOrigins?: readonly string[];
};

export type ClawevolveModule = {
  publicRouter: Router;
  internalRouter: Router;
  taskGuardRouter: Router;
  internalApi: ClawEvolveInternalApi;
  repositories: {
    evolve: EvolveRepository;
    taskSource: EvolveTaskSourceRepository;
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
  const taskSource = new EvolveTaskSourceRepository(db);
  const clawInsight = options.clawInsight ?? null;
  const improvement = clawInsight?.improvementRepository ?? null;
  const autoRepair = clawInsight?.autoRepairRepository ?? null;
  const benchDomain = new BenchDomainRepository(db);
  const benchTemplate = new BenchTemplateRepository(db);
  const benchRun = new BenchRunRepository(db);
  const botWorkflowPermission = new BotWorkflowPermissionRepository(db);
  const taskSourceService = clawInsight?.readFrozenEvidence
    ? new TaskSourceService(taskSource, clawInsight.readFrozenEvidence)
    : null;
  const dispatch = options.dispatch ?? dispatchEvolveCommand;
  const insightPlanStepService = new InsightPlanStepService(evolve, dispatch);
  const governance = clawInsight?.governanceRuleProvider ?? null;
  const insightTaskService = clawInsight && taskSourceService
    ? new InsightTaskService(
        evolve,
        clawInsight.improvementRepository,
        taskSourceService,
        insightPlanStepService,
        autoRepair,
        governance,
      )
    : null;

  const publicRouter = createEvolveRouter(evolve, {
    db,
    dispatch,
    dispatchTaskLogArchive: options.dispatchTaskLogArchive,
    cancelExecution: options.cancelExecution,
    improvementRepo: improvement,
    taskSourceService,
    insightPlanStepService,
    insightTaskService,
    autoRepairRepo: autoRepair,
    ruleProvider: governance,
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
    flowRunRepo: new FlowRunRepository(db),
    nodeExecRepo: new NodeExecutionRepository(db),
    nodeStepTraceRepo: new NodeStepTraceRepository(db),
    runLogRepo: new RunLogRepository(db),
    executionStepLogRepo: new ExecutionStepLogRepository(db),
  });
  const taskGuardRouter = createInternalTaskGuardRouter(evolve);

  let runAnalysisTimeoutTimer: NodeJS.Timeout | null = null;
  let suggestionApplyTimeoutTimer: NodeJS.Timeout | null = null;

  return {
    publicRouter,
    internalRouter,
    taskGuardRouter,
    internalApi: {
      async createInsightTask(input) {
        if (!insightTaskService) throw new Error("ClawInsight integration is unavailable");
        return insightTaskService.create(input);
      },
    },
    repositories: { evolve, taskSource },
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
