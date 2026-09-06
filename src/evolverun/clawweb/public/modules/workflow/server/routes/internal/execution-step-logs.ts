/**
 * Internal API routes for execution_step_log — write and read operations for ClawMind.
 * Mounted at /api/internal/execution-step-logs
 */
import { Router, type Request, type Response } from "express";
import { ExecutionStepLogRepository } from "@avernet/workflow/server/repositories/execution-step-log-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalExecutionStepLogsRouter(
  stepLogRepo: ExecutionStepLogRepository | null,
): Router {
  const router = Router();

  /** POST / — insert a single execution step log entry */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    const body = req.body as {
      flow_id?: string;
      node_id?: string;
      step_type?: string;
      timestamp?: number;
      input_summary?: string | null;
      output_summary?: string | null;
      llm_evaluation?: string | null;
      decision_path?: string | null;
      duration_ms?: number | null;
      token_usage?: number | null;
      metadata?: Record<string, unknown> | null;
    };

    if (!stepLogRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!body.flow_id || !body.node_id || !body.step_type || body.timestamp === undefined) {
      res
        .status(400)
        .json({ success: false, error: "Bad Request", message: "Missing required fields: flow_id, node_id, step_type, timestamp" });
      return;
    }

    const ok = await stepLogRepo.insertStep({
      flowId: body.flow_id,
      nodeId: body.node_id,
      stepType: body.step_type as "start" | "complete" | "fail" | "retry" | "skip" | "materialize" | "inject" | "llm_evaluate" | "goal_check" | "replan" | "budget_check" | "budget_warning" | "budget_exhausted",
      timestamp: body.timestamp,
      inputSummary: body.input_summary ?? null,
      outputSummary: body.output_summary ?? null,
      llmEvaluation: body.llm_evaluation ?? null,
      decisionPath: body.decision_path ?? null,
      durationMs: body.duration_ms ?? null,
      tokenUsage: body.token_usage ?? null,
      metadata: body.metadata ? JSON.stringify(body.metadata) : null,
    });

    apiLog("WRITE", "/execution-step-logs", { flowId: body.flow_id, nodeId: body.node_id, stepType: body.step_type, ok });
    res.json({ success: ok, inserted: ok ? 1 : 0 });
  }));

  /** GET / — query step logs for a flow */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    const { flowId, nodeId, stepType, limit, offset } = req.query as {
      flowId?: string;
      nodeId?: string;
      stepType?: string;
      limit?: string;
      offset?: string;
    };

    if (!stepLogRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!flowId) {
      res
        .status(400)
        .json({ success: false, error: "Bad Request", message: "Missing flowId query parameter" });
      return;
    }

    const steps = await stepLogRepo.getStepsByFlow(flowId, {
      nodeId: nodeId ?? undefined,
      stepType: stepType ?? undefined,
      limit: limit ? parseInt(limit, 10) : undefined,
      offset: offset ? parseInt(offset, 10) : undefined,
    });

    res.json({ success: true, data: steps });
  }));

  /** GET /count — count step logs for a flow */
  router.get("/count", asyncHandler(async (req: Request, res: Response) => {
    const { flowId, nodeId, stepType } = req.query as {
      flowId?: string;
      nodeId?: string;
      stepType?: string;
    };

    if (!stepLogRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!flowId) {
      res
        .status(400)
        .json({ success: false, error: "Bad Request", message: "Missing flowId query parameter" });
      return;
    }

    const count = await stepLogRepo.getStepCountByFlow(flowId, {
      nodeId: nodeId ?? undefined,
      stepType: stepType ?? undefined,
    });

    res.json({ success: true, count });
  }));

  /** POST /cleanup — delete step logs older than a given timestamp */
  router.post("/cleanup", asyncHandler(async (req: Request, res: Response) => {
    const { older_than } = req.body as { older_than?: number };

    if (!stepLogRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (older_than === undefined) {
      res
        .status(400)
        .json({ success: false, error: "Bad Request", message: "Missing older_than field" });
      return;
    }

    const deleted = await stepLogRepo.deleteOlderThan(older_than);
    apiLog("WRITE", "/execution-step-logs/cleanup", { olderThan: older_than, deleted });
    res.json({ success: true, deleted });
  }));

  return router;
}