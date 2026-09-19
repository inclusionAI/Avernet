/**
 * Express-based query API server for ClawFlow.
 *
 * Provides REST endpoints for querying workflow state, events,
 * node executions, metrics, and alerts from ZDAS.
 * Also provides ClawWeb endpoints for packs, workflow specs, and dry-run.
 * Gated behind `api.enabled` config and API key authentication.
 */
import express, { type Request, type Response, type NextFunction } from "express";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ApiConfig } from "../config/types.js";
import type { IFlowRunRepository, IFlowEventRepository, INodeExecutionRepository, IFlowMetricsRepository, ITriggeredAlertRepository, IFacadeBindingRepository } from "../db/repositories/types.js";
import type { WebhookConfig } from "../config/types.js";
import type { WebhookTriggerRepository } from "../db/repositories/webhook-trigger-repository.js";
import type { WebhookEventRepository } from "../db/repositories/webhook-event-repository.js";
import type { WorkflowLauncher } from "../webhook/trigger-adapter.js";
import type { ControllerDeps } from "../controller.js";
import { createFlowsRouter } from "./routes/flows.js";
import { createEventsRouter } from "./routes/events.js";
import { createNodesRouter } from "./routes/nodes.js";
import { createMetricsRouter } from "./routes/metrics.js";
import { createAlertsRouter } from "./routes/alerts.js";
import { createWebhookRouter } from "../webhook/router.js";
import { createRunsRouter } from "./routes/runs.js";
import { createRunArchiveRouter } from "./routes/run-archives.js";
import { createRunNodesRouter } from "./routes/run-nodes.js";
import { createRunEventsRouter } from "./routes/run-events.js";
import { createPacksRouter } from "./routes/packs.js";
import { createDryRunRouter } from "./routes/dry-run.js";
import { createFacadesRouter } from "./routes/facades.js";
import { createFlowControlRouter } from "./routes/flow-control.js";
import { getFlowControlService } from "../flow-control/index.js";
import { createCallbackRouter, type CallbackRouterDeps } from "../callback/router.js";
import type { AsyncCallbackConfig } from "../config/types.js";

/** Repositories injected into route handlers. */
export type ApiRepositories = {
  flowRunRepository: IFlowRunRepository | null;
  eventRepository: IFlowEventRepository | null;
  nodeExecutionRepository: INodeExecutionRepository | null;
  metricsRepository: IFlowMetricsRepository | null;
  alertRepository: ITriggeredAlertRepository | null;
  facadeBindingRepository: IFacadeBindingRepository | null;
  runArchiveBuilder?: { buildArchive(flowId: string): Promise<unknown> } | null;
};

/** Webhook dependencies for the webhook receive endpoint. */
export type WebhookDeps = {
  config: WebhookConfig;
  triggerStore: WebhookTriggerRepository;
  eventStore: WebhookEventRepository;
  launchWorkflow: WorkflowLauncher;
};

/** ClawWeb static file serving options. */
export type ClawWebConfig = {
  /** Path to the ClawWeb build output directory. Defaults to my_skills/clawweb/dist/ */
  staticDir?: string;
};

/** Dependencies for the approval card callback endpoint. */
export type ApprovalCallbackDeps = {
  controllerDeps: ControllerDeps;
};

/** Dependencies for the async-callback HTTP endpoint. */
export type AsyncCallbackDeps = CallbackRouterDeps;

/** Create and configure the Express application. */
export function createApp(
  config: ApiConfig,
  repos: ApiRepositories,
  webhookDeps?: WebhookDeps,
  clawWeb?: ClawWebConfig,
  approvalDeps?: ApprovalCallbackDeps,
  asyncCallbackDeps?: AsyncCallbackDeps,
): express.Express {
  const app = express();

  app.use(express.json());

  // Webhook receive endpoint: NO API key auth — external services POST here
  if (webhookDeps) {
    app.use("/api/webhooks", createWebhookRouter(webhookDeps));
  }

  // Approval card callback endpoint: NO API key auth — called by DingTalk connector (localhost)
  // This must be registered before the API key middleware so it doesn't require auth.
  if (approvalDeps) {
    app.use("/api/approval", createApprovalRouter(approvalDeps));
  }

  // Async-callback endpoint: NO API key auth — external business systems call back here.
  // Auth is via HMAC-SHA256 signature or x-one-id header (per-node config).
  // Must be registered before the API key middleware.
  if (asyncCallbackDeps && asyncCallbackDeps.config.enabled) {
    app.use("/api/callback", createCallbackRouter(asyncCallbackDeps));
  }

  // API key authentication middleware (applies to all other /api routes)
  app.use("/api", apiKeyMiddleware(config));

  // Health check (no auth required)
  app.get("/health", (_req: Request, res: Response) => {
    res.json({ status: "ok" });
  });

  // ── Legacy endpoints (kept for backward compatibility) ──
  app.use("/api/flows", createFlowsRouter(repos.flowRunRepository));
  app.use("/api/flows", createEventsRouter(repos.eventRepository));
  app.use("/api/flows", createNodesRouter(repos.nodeExecutionRepository));
  app.use("/api/metrics", createMetricsRouter(repos.metricsRepository));
  app.use("/api/alerts", createAlertsRouter(repos.alertRepository));

  // ── ClawWeb endpoints ──
  app.use("/api/runs", createRunsRouter(repos.flowRunRepository, repos.nodeExecutionRepository));
  app.use("/api/runs", createRunNodesRouter(repos.nodeExecutionRepository));
  app.use("/api/runs", createRunEventsRouter(repos.eventRepository));

  // ── Run archive ──
  app.use("/api/run-archives", createRunArchiveRouter(
    repos.runArchiveBuilder as never,
  ));

  app.use("/api/packs", createPacksRouter(repos.facadeBindingRepository));
  app.use("/api/dry-run", createDryRunRouter());
  app.use("/api/facades", createFacadesRouter(repos.facadeBindingRepository));

  // ── Flow control monitoring (service may be null if disabled) ──
  app.use("/api/flow-control", createFlowControlRouter(getFlowControlService()));

  // ── ClawWeb static file serving ──
  const staticDir = clawWeb?.staticDir ?? join(process.cwd(), "my_skills", "clawweb", "dist");
  if (existsSync(staticDir)) {
    app.use(express.static(staticDir));
    // SPA fallback: serve index.html for non-API routes
    app.get("*", (_req: Request, res: Response) => {
      const indexPath = join(staticDir, "index.html");
      if (existsSync(indexPath)) {
        res.sendFile(indexPath);
      } else {
        res.status(404).json({ error: "Not Found" });
      }
    });
  }

  return app;
}

/** Start the API server. Returns the http.Server instance. */
export function startApiServer(
  config: ApiConfig,
  repos: ApiRepositories,
  webhookDeps?: WebhookDeps,
  clawWeb?: ClawWebConfig,
  approvalDeps?: ApprovalCallbackDeps,
): import("http").Server | null {
  if (!config.enabled) {
    return null;
  }

  const app = createApp(config, repos, webhookDeps, clawWeb, approvalDeps);
  const server = app.listen(config.port, config.host, () => {
    console.log(`[api] Query API listening on ${config.host}:${config.port}`);
  });

  return server;
}

/** Create the approval callback router (no API key auth — called by DingTalk connector). */
function createApprovalRouter(deps: ApprovalCallbackDeps): express.Router {
  const router = express.Router();

  // Lazy-load the callback handler to avoid circular imports at module level
  let handler: typeof import("../card/approval-card-callback-handler.js").handleApprovalCardCallback | null = null;

  router.post("/callback", async (req: Request, res: Response) => {
    try {
      const { outTrackId, action, userId } = req.body;

      // Validate required fields
      if (!outTrackId || typeof outTrackId !== "string") {
        res.status(400).json({ ok: false, error: "missing_or_invalid", message: "outTrackId is required" });
        return;
      }
      if (action !== "approve" && action !== "reject") {
        res.status(400).json({ ok: false, error: "invalid_action", message: "action must be 'approve' or 'reject'" });
        return;
      }
      if (!userId || typeof userId !== "string") {
        res.status(400).json({ ok: false, error: "missing_or_invalid", message: "userId is required" });
        return;
      }

      if (!handler) {
        handler = (await import("../card/approval-card-callback-handler.js")).handleApprovalCardCallback;
      }

      const result = await handler(deps.controllerDeps, { outTrackId, action, userId });
      const statusCode = result.ok
        ? 200
        : result.error === "approval_card_not_found"
          ? 404
          : result.error === "already_resolved"
            ? 409
            : result.error === "unauthorized"
              ? 403
              : 500;

      res.status(statusCode).json(result);
    } catch (err) {
      console.error("[api] /api/approval/callback error", err);
      res.status(500).json({ ok: false, error: "internal_error", message: err instanceof Error ? err.message : "Unknown error" });
    }
  });

  return router;
}

/** API key authentication middleware. */
function apiKeyMiddleware(config: ApiConfig): (req: Request, res: Response, next: NextFunction) => void {
  const validKey = process.env.WORKFLOW_API_KEY ?? config.apiKey;

  return (req: Request, res: Response, next: NextFunction) => {
    if (!validKey) {
      // No API key configured — allow all requests
      next();
      return;
    }

    const provided = req.headers["x-api-key"];
    if (provided === validKey) {
      next();
      return;
    }

    res.status(401).json({ error: "Unauthorized", message: "Invalid or missing API key" });
  };
}