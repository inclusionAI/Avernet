/**
 * Internal API routes for http_callback_logs — write and read operations for ClawMind.
 * Mounted at /api/internal/http-callback-logs
 *
 * POST   /              — insert a log entry
 * GET    /flow/:flowId  — find by flow ID
 * DELETE /cleanup       — delete records older than timestamp
 *
 * Read-only queries (by workflowId, status) are in reads.ts at:
 *   GET /reads/http-callback-logs/workflow/:workflowId
 *   GET /reads/http-callback-logs/status/:status
 */
import { Router, type Request, type Response } from "express";
import { HttpCallbackLogRepository } from "@avernet/workflow/server/repositories/http-callback-log-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalHttpCallbackLogsRouter(
  httpCallbackLogRepo: HttpCallbackLogRepository | null,
): Router {
  const router = Router();

  /** POST / — insert a callback log entry */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    const body = req.body as Record<string, unknown>;

    if (!httpCallbackLogRepo) {
      apiLog("WRITE", "/http-callback-logs", { status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!body.flow_id || !body.workflow_id || !body.config_id || !body.callback_url || !body.notify_event) {
      res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields" });
      return;
    }

    try {
      const insertId = await httpCallbackLogRepo.insert({
        flow_id: String(body.flow_id),
        workflow_id: String(body.workflow_id),
        config_id: String(body.config_id),
        config_name: body.config_name ? String(body.config_name) : null,
        callback_url: String(body.callback_url),
        notify_event: String(body.notify_event),
        node_id: body.node_id ? String(body.node_id) : null,
        attempt: Number(body.attempt ?? 0),
        max_attempts: Number(body.max_attempts ?? 1),
        request_body: body.request_body ? String(body.request_body) : null,
        request_headers: body.request_headers ? String(body.request_headers) : null,
        response_status_code: body.response_status_code != null ? Number(body.response_status_code) : null,
        response_body: body.response_body ? String(body.response_body) : null,
        duration_ms: body.duration_ms != null ? Number(body.duration_ms) : null,
        status: String(body.status ?? "sent"),
        error_message: body.error_message ? String(body.error_message) : null,
        callbackSource: body.callback_source ? String(body.callback_source) : 'workflow-level',
      });
      apiLog("WRITE", "/http-callback-logs", { status: 201, insertId });
      res.status(201).json({ success: true, data: { insertId } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/http-callback-logs", { status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /flow/:flowId — find logs by flow ID */
  router.get("/flow/:flowId", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId ?? "");
    const limit = Math.min(Number(req.query.limit ?? 100), 500);

    if (!httpCallbackLogRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      const rows = await httpCallbackLogRepo.findByFlowId(flowId, limit);
      apiLog("READ", "/http-callback-logs/flow/:flowId", { flowId, status: 200, count: rows.length });
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/http-callback-logs/flow/:flowId", { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /cleanup — delete records older than timestamp */
  router.delete("/cleanup", asyncHandler(async (req: Request, res: Response) => {
    const olderThan = Number(req.query.olderThan);

    if (!httpCallbackLogRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!olderThan) {
      res.status(400).json({ success: false, error: "Bad Request", message: "Missing olderThan parameter" });
      return;
    }

    try {
      const deleted = await httpCallbackLogRepo.deleteOlderThan(olderThan);
      apiLog("DELETE", "/http-callback-logs/cleanup", { olderThan, status: 200, deleted });
      res.json({ success: true, data: { deleted } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("DELETE", "/http-callback-logs/cleanup", { olderThan, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}