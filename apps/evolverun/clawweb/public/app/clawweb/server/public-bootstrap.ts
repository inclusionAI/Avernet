import express from "express";
import compression from "compression";
import cookieParser from "cookie-parser";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createClawevolveModule } from "@avernet/clawevolve";
import { createInsightRuntime, createInsightRouter } from "@avernet/clawinsight";
import { createApprovalRouter } from "@avernet/workflow/server/routes/approval";
import { createInternalApprovalCardsRouter } from "@avernet/workflow/server/routes/internal/approval-cards";
import { ApprovalCardRepository } from "@avernet/workflow/server/repositories/approval-card-repository";
import {
  closeDatabase,
  initDatabase,
  resolveAdminConfigFrom,
  resolveDynamicAdminConfig,
} from "@avernet/clawweb-shared/server/db";
import { adminAuthMiddleware } from "@avernet/clawweb-shared/server/middleware/admin-auth";
import { AdminUserRepository } from "@avernet/clawweb-shared/server/repositories/admin-user-repository";
import type { ClawWebBootstrap } from "./bootstrap.js";
import { createAuthMeHandler, resolveAuthMeIdentity } from "./auth-me.js";
import { createWorkflowRepairRuntime } from "./repair-workbench-runtime.js";
import { AistudioService, resolveAistudioConfig } from "@avernet/clawinsight/server/services/aistudio-service";
import { createRepairAisExecution, RepairGitStore } from "./repair-ais-execution.js";

export function createClawWebBootstrap(): ClawWebBootstrap {
  return {
    async start(context) {
      const port = Number.parseInt(process.env.PORT ?? "3001", 10);
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
      const aisConfig = resolveAistudioConfig();
      const repairSnapshotId = Number(process.env.CLAWWEB_REPAIR_AIS_SNAPSHOT_ID);
      const repairGitDir = process.env.CLAWWEB_REPAIR_CLAWFLOWS_GIT_DIR?.trim();
      let repairExecution: ReturnType<typeof createRepairAisExecution> | undefined;
      if (aisConfig.token && Number.isSafeInteger(repairSnapshotId) && repairSnapshotId > 0
        && repairGitDir && existsSync(repairGitDir)) {
        const gitStore = new RepairGitStore(repairGitDir);
        try {
          await gitStore.verify();
          repairExecution = createRepairAisExecution(db, new AistudioService(aisConfig), gitStore, repairSnapshotId);
        } catch {
          console.warn('[clawweb] Workflow Repair Git mirror is invalid; generation is disabled');
        }
      }
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
