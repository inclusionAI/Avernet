/**
 * Evolvetrace Express server — standalone backend for task护航 and TcLog.
 */
import express from "express";
import cors from "cors";
import compression from "compression";
import cookieParser from "cookie-parser";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse as parseYaml } from "yaml";
import { initDatabase, closeDatabase, resolveSignatureConfig, resolveAdminConfig } from "./db.js";
import type { EvolvetraceExtensions } from "./extensions/types.js";
import { FlowRunRepository } from "./repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "./repositories/node-execution-repository.js";
import { FlowEventRepository } from "./repositories/event-repository.js";
import { WorkflowSpecRepository } from "./repositories/workflow-spec-repository.js";
import { FacadeBindingRepository } from "./repositories/facade-binding-repository.js";
import { BotWorkflowPermissionRepository } from "./repositories/bot-workflow-permission-repository.js";
import { MetricsRepository } from "./repositories/metrics-repository.js";
import { AlertRepository } from "./repositories/alert-repository.js";
import { FlowControlRepository } from "./repositories/flow-control-repository.js";
import { ExecutionStepLogRepository } from "./repositories/execution-step-log-repository.js";
import { WorkflowNotificationConfigRepository } from "./repositories/workflow-notification-config-repository.js";
import { WorkflowDeployHistoryRepository } from "./repositories/workflow-deploy-history-repository.js";
import { HttpCallbackConfigRepository } from "./repositories/http-callback-config-repository.js";
import { SandboxQueryService } from "./services/sandbox-query-service.js";
import { createRunsRouter } from "./routes/runs.js";
import { createWorkflowsRouter } from "./routes/workflows.js";
import { createAuthRouter } from "./routes/auth.js";
import { createTCLogRouter } from "./routes/tclog.js";
import { createSandboxQueryRouter } from "./routes/sandbox-query.js";
import { createFacadesRouter } from "./routes/facades.js";
import { createInternalRouter, type InternalRepos } from "./routes/internal/index.js";
import { signatureMiddleware } from "./middleware/signature.js";
import { adminAuthMiddleware } from "./middleware/admin-auth.js";
import { requestLogger } from "./middleware/request-logger.js";
import { errorLogger } from "./middleware/error-logger.js";
import { initSchema } from "./schema.js";
import { getCorsAllowedOrigins } from "./env.js";

function resolvePort(): number {
  const envPort = process.env.PORT;
  if (envPort) {
    const parsed = parseInt(envPort, 10);
    if (!Number.isNaN(parsed)) return parsed;
  }
  const candidate = join(process.cwd(), "configs", "application.yaml");
  try {
    if (existsSync(candidate)) {
      const yaml = parseYaml(readFileSync(candidate, "utf-8")) as Record<string, unknown>;
      const serverConfig = (yaml.server ?? {}) as Record<string, unknown>;
      const port = serverConfig.port;
      if (typeof port === "number") return port;
      if (typeof port === "string") {
        const parsed = parseInt(port, 10);
        if (!Number.isNaN(parsed)) return parsed;
      }
    }
  } catch {
    // ignore YAML parse errors
  }
  return 3001;
}

const PORT = resolvePort();

async function main(extensions?: EvolvetraceExtensions): Promise<void> {
  // 数据库初始化 — 优先使用扩展提供的 createDatabase，否则回退社区默认
  const db = extensions?.createDatabase
    ? await extensions.createDatabase(undefined)
    : await initDatabase();
  await initSchema(db);

  const hasDb = db.dbType !== "noop";

  const flowRunRepo = hasDb ? new FlowRunRepository(db) : null;
  const nodeExecRepo = hasDb ? new NodeExecutionRepository(db) : null;
  const eventRepo = hasDb ? new FlowEventRepository(db) : null;
  const workflowSpecRepo = hasDb ? new WorkflowSpecRepository(db) : null;
  const facadeRepo = hasDb ? new FacadeBindingRepository(db) : null;
  const botPermRepo = hasDb ? new BotWorkflowPermissionRepository(db) : null;
  const metricsRepo = hasDb ? new MetricsRepository(db) : null;
  const alertRepo = hasDb ? new AlertRepository(db) : null;
  const flowControlRepo = hasDb ? new FlowControlRepository(db) : null;
  const executionStepLogRepo = hasDb ? new ExecutionStepLogRepository(db) : null;
  const notificationConfigRepo = hasDb ? new WorkflowNotificationConfigRepository(db) : null;
  const wfdhRepo = hasDb ? new WorkflowDeployHistoryRepository(db) : null;
  const httpCallbackConfigRepo = hasDb ? new HttpCallbackConfigRepository(db) : null;
  const sandboxQueryService = new SandboxQueryService();

  const app = express();
  const allowedCorsOrigins = getCorsAllowedOrigins();

  app.use(cors({
    origin: (origin, callback) => {
      if (!origin) return callback(null, "*");
      if (/^https?:\/\/localhost(:\d+)?$/.test(origin)) return callback(null, origin);
      if (/^https?:\/\/127\.0\.0\.1(:\d+)?$/.test(origin)) return callback(null, origin);
      if (allowedCorsOrigins.has(origin)) return callback(null, origin);
      callback(new Error("CORS not allowed"));
    },
    credentials: true,
  }));
  app.use(compression());
  app.use(express.json({ limit: "10mb" }));
  app.use(cookieParser());

  const adminConfig = resolveAdminConfig();
  app.use(adminAuthMiddleware(adminConfig));

  app.use(requestLogger);

  // 企业扩展中间件注册（在标准中间件之后、路由注册之前）
  extensions?.registerMiddleware?.(app);

  app.get("/health", (_req, res) => {
    res.json({ status: "ok", db: db.dbType });
  });

  app.use("/api/auth", createAuthRouter());
  app.use("/api/runs", createRunsRouter(
    flowRunRepo, nodeExecRepo, eventRepo, metricsRepo, alertRepo,
    flowControlRepo, botPermRepo, workflowSpecRepo, executionStepLogRepo,
  ));
  app.use("/api/workflows", createWorkflowsRouter(
    workflowSpecRepo, facadeRepo, botPermRepo,
    notificationConfigRepo, wfdhRepo, httpCallbackConfigRepo,
    flowRunRepo, nodeExecRepo,
  ));
  app.use("/api/facades", createFacadesRouter(facadeRepo));
  app.use("/api/tclog", createTCLogRouter(db, flowRunRepo, botPermRepo));
  app.use("/api/sandbox-query", createSandboxQueryRouter({ sandboxQueryService }));

  // 企业扩展路由注册（在标准路由注册之后）
  extensions?.registerRoutes?.(app);

  const internalRepos: InternalRepos = {
    flowRunRepo,
    nodeExecRepo,
    eventRepo,
    facadeBindingRepo: facadeRepo,
    workflowSpecRepo,
    botWorkflowPermissionRepo: botPermRepo,
    workflowDeployHistoryRepo: wfdhRepo,
  };
  const sigConfig = resolveSignatureConfig();
  app.use("/api/internal", signatureMiddleware(sigConfig), createInternalRouter(internalRepos));

  app.use(errorLogger);

  const staticDir = join(import.meta.dirname, "..", "dist");
  if (existsSync(staticDir)) {
    app.use(express.static(staticDir, { maxAge: "1d", immutable: false, index: false }));
    app.get("{*path}", (req, res) => {
      if (req.path.startsWith("/api/")) {
        res.status(404).json({ error: "Not Found" });
        return;
      }
      const indexPath = join(staticDir, "index.html");
      if (existsSync(indexPath)) {
        let html = readFileSync(indexPath, "utf-8");
        const reqPayload = JSON.stringify({
          userId: req.userId ?? null,
          isAdmin: req.isAdmin ?? false,
          isLogAdmin: req.isLogAdmin ?? false,
        });
        html = html.replace(
          "<head>",
          `<head><script>window.__REQ__ = ${reqPayload};</script>`,
        );
        res.set("Content-Type", "text/html; charset=utf-8");
        res.send(html);
      } else {
        res.status(404).json({ error: "Not Found" });
      }
    });
  }

  const server = app.listen(PORT, () => {
    console.log(`[evolvetrace] Server listening on http://localhost:${PORT}`);
    console.log(`[evolvetrace] Database mode: ${db.dbType}`);
    console.log(`[evolvetrace] Static serving: ${existsSync(staticDir) ? "enabled" : "disabled (no dist/)"}`);
  });

  server.keepAliveTimeout = 61 * 1000;
  server.headersTimeout = 65 * 1000;

  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log("[evolvetrace] Shutting down...");
    setTimeout(() => process.exit(0), 3000);
    try {
      server.close();
      await closeDatabase();
    } catch {
      // ignore
    }
    process.exit(0);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

const extensions: EvolvetraceExtensions | undefined = undefined; // 社区版不注入扩展
main(extensions).catch((err) => {
  console.error("[evolvetrace] Failed to start server:", err);
  process.exit(1);
});
