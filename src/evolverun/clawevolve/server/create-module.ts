import { fileURLToPath } from "node:url";
import type { Router } from "express";
import type { IDatabase } from "./db.js";
import { EvolveRepository } from "./repositories/evolve-repository.js";
import { EvolveTaskSourceRepository } from "./repositories/evolve-task-source-repository.js";
import { InsightImprovementRepository } from "./repositories/insight-improvement-repository.js";
import { InsightAutoRepairRepository } from "./repositories/insight-auto-repair-repository.js";
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
import { TaskSourceService, type FrozenEvidenceReader } from "./services/evolve/task-source-service.js";
import { dispatchEvolveCommand } from "./services/evolve-dispatcher.js";
import { InsightPlanStepService } from "./services/evolve/insight-plan-step-service.js";
import { InsightTaskService } from "./services/evolve/insight-task-service.js";
import {
  GovernanceRuleProvider,
  type GovernanceRuleProviderOptions,
} from "./services/insight/governance-rule-provider.js";
import type { ObjectStore } from "./services/object-storage/oss-object-store.js";
import { startRunAnalysisTimeoutSweeper } from "./services/evolve/run-analysis-timeout.js";
import { startSuggestionApplyTimeoutSweeper } from "./services/evolve/suggestion-apply-timeout.js";
import { configureClawWebPublicBaseUrl } from "./env.js";

export type ClawevolveModuleOptions = {
  db: IDatabase;
  dispatch?: EvolveRouterDeps["dispatch"];
  dispatchTaskLogArchive?: EvolveRouterDeps["dispatchTaskLogArchive"];
  cancelExecution?: EvolveRouterDeps["cancelExecution"];
  artifactStore?: ObjectStore;
  frozenEvidenceReader?: FrozenEvidenceReader;
  publicBaseUrl?: string;
  trustedPublicOrigins?: readonly string[];
  governance?: GovernanceRuleProviderOptions;
};

export type ClawevolveModule = {
  publicRouter: Router;
  internalRouter: Router;
  taskGuardRouter: Router;
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

  const evolve = new EvolveRepository(db);
  const taskSource = new EvolveTaskSourceRepository(db);
  const improvement = new InsightImprovementRepository(db);
  const autoRepair = new InsightAutoRepairRepository(db);
  const benchDomain = new BenchDomainRepository(db);
  const benchTemplate = new BenchTemplateRepository(db);
  const benchRun = new BenchRunRepository(db);
  const botWorkflowPermission = new BotWorkflowPermissionRepository(db);
  const taskSourceService = options.frozenEvidenceReader
    ? new TaskSourceService(taskSource, options.frozenEvidenceReader)
    : null;
  const dispatch = options.dispatch ?? dispatchEvolveCommand;
  const insightPlanStepService = new InsightPlanStepService(evolve, dispatch);
  const governance = options.governance
    ? new GovernanceRuleProvider(options.governance)
    : new GovernanceRuleProvider({
        environment: "pre",
        filePath: fileURLToPath(new URL("./fixtures/insight/v1/governance-rules.json", import.meta.url)),
      });
  const insightTaskService = taskSourceService
    ? new InsightTaskService(
        evolve,
        improvement,
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
