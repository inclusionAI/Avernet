import express from "express";
import compression from "compression";
import cookieParser from "cookie-parser";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createClawevolveModule } from "@avernet/clawevolve";
import { createInsightRuntime, createInsightRouter, createSessionRecoveryRouter, type SessionRecoveryRuntime } from "@avernet/clawinsight";
import { createApprovalRouter } from "@avernet/workflow/server/routes/approval";
import { createInternalApprovalCardsRouter } from "@avernet/workflow/server/routes/internal/approval-cards";
import { ApprovalCardRepository } from "@avernet/workflow/server/repositories/approval-card-repository";
import {
  createAutoHealRouter,
  createRunsRouter,
  createWorkflowsRouter,
} from "@avernet/workflow/server";
import { createWorkflowNodeStatsRouter } from "@avernet/workflow/server/routes/workflow-node-stats";
import { createWorkflowVersionComparisonService } from "@avernet/workflow/server/services/workflow-version-comparison-service";
import { createWorkflowVersionSourceService } from "@avernet/workflow/server/services/workflow-version-source-service";
import { WorkflowSpecRepository } from "@avernet/workflow/server/repositories/workflow-spec-repository";
import { WorkflowDeployHistoryRepository } from "@avernet/workflow/server/repositories/workflow-deploy-history-repository";
import { FlowRunRepository } from "@avernet/workflow/server/repositories/flow-run-repository";
import { NodeExecutionRepository } from "@avernet/workflow/server/repositories/node-execution-repository";
import { FlowEventRepository } from "@avernet/workflow/server/repositories/event-repository";
import { MetricsRepository } from "@avernet/workflow/server/repositories/metrics-repository";
import { AlertRepository } from "@avernet/workflow/server/repositories/alert-repository";
import { FlowControlRepository } from "@avernet/workflow/server/repositories/flow-control-repository";
import { ExecutionStepLogRepository } from "@avernet/workflow/server/repositories/execution-step-log-repository";
import {
  closeDatabase,
  initDatabase,
  resolveAdminConfigFrom,
  resolveDynamicAdminConfig,
  resolveAutoHealConfig,
} from "@avernet/clawweb-shared/server/db";
import { adminAuthMiddleware } from "@avernet/clawweb-shared/server/middleware/admin-auth";
import { AdminUserRepository } from "@avernet/clawweb-shared/server/repositories/admin-user-repository";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { ClawWebBootstrap } from "./bootstrap.js";
import { createAuthMeHandler, resolveAuthMeIdentity } from "./auth-me.js";
import { createWorkflowRepairRuntime } from "./repair-workbench-runtime.js";
import { AistudioService, resolveAistudioConfig } from "@avernet/clawinsight/server/services/aistudio-service";
import { createRepairAisExecution, resolveRepairAisConfiguration } from "./repair-ais-execution.js";
import { createAutoHealTriggerRun } from "./auto-heal-trigger-run.js";

export function createClawWebBootstrap(options: { sessionRecovery?: SessionRecoveryRuntime } = {}): ClawWebBootstrap {
  return {
    async start(context) {
      const port = Number.parseInt(process.env.PORT ?? "3001", 10);
      const aisConfig = resolveAistudioConfig();
      const repairConfig = await resolveRepairAisConfiguration(aisConfig.token,
        process.env.CLAWWEB_REPAIR_AIS_SNAPSHOT_ID, process.env.CLAWWEB_REPAIR_CLAWFLOWS_GIT_DIR);
      const { db } = await initDatabase();
      if (db.dbType === "noop") throw new Error("Public ClawWeb requires an available database");
      const insight = createInsightRuntime(db);
      const clawevolve = createClawevolveModule({ db });
      await clawevolve.start();
      const adminUserRepo = new AdminUserRepository(db);
      const fallbackAdminConfig = resolveAdminConfigFrom(context.config);
      // Seed the configured admin lists into the dynamic roster, then read the enabled one back:
      // runtime grants survive, and YAML stays authoritative for whatever it declares.
      let adminConfig;
      try {
        adminConfig = await resolveDynamicAdminConfig(db, fallbackAdminConfig);
      } catch (err) {
        console.warn(
          `[clawweb] Failed to seed dynamic admin config: ${err instanceof Error ? err.message : String(err)}`,
        );
        adminConfig = fallbackAdminConfig;
      }
      const app = express();
      app.use(compression());
      app.use(express.json({ limit: "10mb" }));
      app.use(cookieParser());
      app.use(adminAuthMiddleware({ config: fallbackAdminConfig, repository: adminUserRepo }));
      app.get("/api/auth/me", createAuthMeHandler({ environment: context.environment }));
      app.get("/health", (_request, response) => {
        response.json({
          status: "ok",
          db: db.dbType,
          clawevolveImplementation: "avernet",
          profile: context.profile,
          environment: context.environment,
        });
      });
      app.use("/api/evolve", clawevolve.publicRouter);
      const repairExecution = repairConfig
        ? createRepairAisExecution(db, new AistudioService(aisConfig), repairConfig.gitStore, repairConfig.snapshotId)
        : undefined;
      const repairRuntime = createWorkflowRepairRuntime(db, {
        principal: async request => {
          const identity = resolveAuthMeIdentity(request, { environment: context.environment });
          if (!identity) return null;
          return { actorId: identity.userId, isAdmin: identity.userId === "dev_local"
            || await adminUserRepo.hasRole([identity.userId, identity.userName], "admin") };
        },
        execution: repairExecution,
      });
      app.use("/api/workflow-repairs", repairRuntime.router);
      let repairPollBusy = false;
      const repairPoll = repairExecution ? setInterval(() => {
        if (repairPollBusy) return;
        repairPollBusy = true;
        void repairRuntime.service.reconcile().catch(() => {
          console.warn('[clawweb] Workflow Repair reconciliation failed; will retry');
        }).finally(() => { repairPollBusy = false; });
      }, 15_000) : null;
      repairPoll?.unref();
      app.use("/api/bench", clawevolve.benchRouter);
      app.use("/api/insight/v1", createSessionRecoveryRouter(options.sessionRecovery));
      app.use("/api/insight/v1", createInsightRouter(insight.service));
      // Approval API: user-facing (GET/POST /api/approval/:id/...) and internal (POST /api/approval-cards/...)
      const approvalCardRepo = new ApprovalCardRepository(db);
      app.use("/api/approval", createApprovalRouter(db, {
        resolveSessionIdentity: async (request) => {
          const identity = resolveAuthMeIdentity(request, { environment: context.environment });
          return identity
            ? { ok: true, userId: identity.userId }
            : { ok: false, error: "未登录或登录状态已失效" };
        },
      }));
      app.use("/api/approval-cards", createInternalApprovalCardsRouter(approvalCardRepo));

      // AutoHeal fallbacks only; the router resolves DB overrides on each save/deploy.
      const autoHealConfig = resolveAutoHealConfig();
      const autoHealGitConfig = {
        remoteUrl: autoHealConfig.clawflowsRemoteUrl,
        username: autoHealConfig.gitUsername,
        email: autoHealConfig.gitAuthorEmail,
        token: autoHealConfig.gitToken,
      };
      const autoHealAuthor = {
        name: autoHealConfig.gitAuthorName,
        email: autoHealConfig.gitAuthorEmail,
      };
      const botPermRepo = new BotWorkflowPermissionRepository(db);
      const deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
      const workflowSpecRepo = new WorkflowSpecRepository(db, deployHistoryRepo);
      const flowRunRepo = new FlowRunRepository(db);
      const nodeExecRepo = new NodeExecutionRepository(db);
      const eventRepo = new FlowEventRepository(db);
      const metricsRepo = new MetricsRepository(db);
      const alertRepo = new AlertRepository(db);
      const flowControlRepo = new FlowControlRepository(db);
      const execStepLogRepo = new ExecutionStepLogRepository(db);
      app.use("/api/workflows", createWorkflowsRouter(
        workflowSpecRepo,
        null,
        botPermRepo,
        null,
        deployHistoryRepo,
        null,
        createWorkflowVersionSourceService(db),
      ));
      app.use("/api/workflows", createWorkflowNodeStatsRouter(
        null, null, createWorkflowVersionComparisonService(db), botPermRepo,
      ));
      app.use("/api/runs", createRunsRouter(
        flowRunRepo,
        nodeExecRepo,
        eventRepo,
        metricsRepo,
        alertRepo,
        flowControlRepo,
        botPermRepo,
        workflowSpecRepo,
        execStepLogRepo,
      ));
      app.use("/api/auto-heal", createAutoHealRouter({
        db,
        workflowSpecRepo,
        deployHistoryRepo,
        botPermRepo,
        gitConfig: autoHealGitConfig,
        gitAuthor: autoHealAuthor,
        triggerRun: createAutoHealTriggerRun(),
      }));

      const staticDir = resolve(dirname(fileURLToPath(import.meta.url)), "../web");
      if (existsSync(staticDir)) {
        app.use(express.static(staticDir));
        app.get("{*path}", (request, response) => {
          if (request.path.startsWith("/api/")) return response.status(404).json({ error: "Not Found" });
          return response.sendFile(join(staticDir, "index.html"));
        });
      }
      const server = app.listen(port, "127.0.0.1", () => {
        console.info(`[clawweb] Public runner listening on http://127.0.0.1:${port}`);
      });
      return {
        stop: async () => {
          if (repairPoll) clearInterval(repairPoll);
          await new Promise<void>((done) => server.close(() => done()));
          await clawevolve.stop();
          await closeDatabase();
        },
      };
    },
  };
}
