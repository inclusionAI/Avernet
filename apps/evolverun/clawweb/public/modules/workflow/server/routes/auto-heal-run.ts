/** POST /run only: shared by the public and OCB hosts. See docs/auto-heal-source-run.md. */
import { Router } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import { AutoHealRunError, AutoHealRunService, type AutoHealRunDependencies,
  type AutoHealRunResult } from "../services/auto-heal-run-service.js";

export type { AutoHealRunResult, AutoHealRunSender } from "../services/auto-heal-run-service.js";

export interface AutoHealRunRouterDependencies extends AutoHealRunDependencies {
  /** Best effort after acceptance: audit failure must not invite a duplicate delivery. */
  onAccepted?: (result: AutoHealRunResult, input: {
    workflowId: string;
    sourceFlowId: string;
    actorId: string;
    params?: Record<string, unknown>;
  }) => Promise<void>;
}

const failures = {
  bad_request: [400, "Bad Request"], unauthorized: [401, "Unauthorized"], forbidden: [403, "Forbidden"],
  unavailable: [503, "Service Unavailable"], not_found: [404, "Not Found"],
  invalid_source: [422, "Unprocessable Entity"], delivery_failed: [502, "Bad Gateway"],
  token_expired: [401, "Unauthorized"],
} as const;

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function createAutoHealRunRouter(deps: AutoHealRunRouterDependencies): Router {
  const router = Router();
  const service = new AutoHealRunService(deps);
  router.post("/run", asyncHandler(async (req, res) => {
    try {
      const actorId = resolveWorkflowActorId(req);
      if (!actorId) throw new AutoHealRunError("unauthorized", "User identity required");
      const body: unknown = req.body;
      if (!isObject(body) || typeof body.workflowId !== "string" || !body.workflowId.trim()
        || typeof body.sourceFlowId !== "string" || !body.sourceFlowId.trim()) {
        throw new AutoHealRunError("bad_request", "workflowId and sourceFlowId are required strings");
      }
      if (body.params !== undefined && !isObject(body.params)) {
        throw new AutoHealRunError("bad_request", "params must be an object when supplied");
      }
      const input = { workflowId: body.workflowId.trim(), sourceFlowId: body.sourceFlowId.trim(), actorId,
        ...(body.params !== undefined ? { params: body.params as Record<string, unknown> } : {}) };
      const result = await service.run({ ...input, isAdmin: req.isAdmin === true,
        userName: req.header("X-User-Name")?.trim() || actorId });
      if (deps.onAccepted) {
        try {
          await deps.onAccepted(result, input);
        } catch {
          console.warn("[auto-heal] Run accepted but acceptance audit failed");
        }
      }
      res.json(result);
    } catch (error) {
      const [status, label] = error instanceof AutoHealRunError ? failures[error.code] : [500, "Internal Server Error"];
      res.status(status).json({ ok: false, error: label,
        message: error instanceof Error ? error.message : "Auto-heal run failed",
        ...(error instanceof AutoHealRunError && error.code === "token_expired" ? { tokenExpired: true } : {}) });
    }
  }));
  return router;
}
