/**
 * Internal API routes for flow_retry_requests — the cross-engine retry mailbox.
 * Requesters post a row; every engine instance polls/claims via CAS; the
 * instance holding the flow's TaskFlow state executes and completes it.
 */
import { Router, type Request, type Response } from "express";
import { FlowRetryRequestRepository } from "../../repositories/flow-retry-request-repository.js";
import { apiLog, apiLogBody } from "../internal-logger.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalRetryRequestsRouter(retryRequestRepo: FlowRetryRequestRepository | null): Router {
  const router = Router();
  router.get("/capabilities", (_req, res) => res.json({ success: true, data: { executionOptionsVersion: 1 } }));

  /** POST / — post a retry request (dedupes on an open request for the same flow) */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    apiLogBody("WRITE", "/retry-requests", req.body, {});
    if (!retryRequestRepo) {
      apiLog("WRITE", "/retry-requests", { status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { request_id, flow_id, node_id, reason, requester, options } = req.body as {
        request_id?: string; flow_id?: string; node_id?: string; reason?: string; requester?: string; options?: import("../../repositories/flow-retry-request-repository.js").RetryExecutionOptions;
      };
      if (!request_id || !flow_id) {
        apiLog("WRITE", "/retry-requests", { status: 400, error: "Missing request_id or flow_id" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: request_id, flow_id" });
        return;
      }
      if (options !== undefined && (!options || typeof options !== "object" || Array.isArray(options)
        || Object.keys(options).some(k => !["useCurrentDef", "debug", "inputOverrides"].includes(k))
        || (options.useCurrentDef !== undefined && typeof options.useCurrentDef !== "boolean")
        || (options.debug !== undefined && typeof options.debug !== "boolean")
        || (options.inputOverrides !== undefined && (!options.inputOverrides || typeof options.inputOverrides !== "object" || Array.isArray(options.inputOverrides) || Object.values(options.inputOverrides).some(v => typeof v !== "string")))
        || JSON.stringify(options).length > 64000)) {
        res.status(400).json({ success: false, message: "Invalid retry execution options" }); return;
      }
      const row = await retryRequestRepo.create({ requestId: request_id, flowId: flow_id, nodeId: node_id ?? null, reason: reason ?? null, requester: requester ?? null, options });
      if (!row) {
        apiLog("WRITE", "/retry-requests", { status: 500, error: "create returned null" });
        res.status(500).json({ success: false, error: "Internal Server Error", message: "Failed to create retry request" });
        return;
      }
      apiLog("WRITE", "/retry-requests", { status: 200, requestId: row.request_id, flowId: row.flow_id, deduped: row.request_id !== request_id });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/retry-requests", { status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /claim — CAS-claim open requests; only winners are returned */
  router.post("/claim", asyncHandler(async (req: Request, res: Response) => {
    apiLogBody("WRITE", "/retry-requests/claim", req.body, {});
    if (!retryRequestRepo) {
      apiLog("WRITE", "/retry-requests/claim", { status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { limit, lease_secs, claim_engine, flow_ids, options_version } = req.body as { limit?: number; lease_secs?: number; claim_engine?: string; flow_ids?: string[]; options_version?: number };
      if (!claim_engine) {
        apiLog("WRITE", "/retry-requests/claim", { status: 400, error: "Missing claim_engine" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: claim_engine" });
        return;
      }
      const safeLimit = Math.min(Math.max(Number(limit) || 10, 1), 50);
      const safeLease = Math.max(Number(lease_secs) || 600, 60);
      const safeFlowIds = Array.isArray(flow_ids)
        ? flow_ids.filter((id): id is string => typeof id === "string" && id.length > 0)
        : undefined;
      const claimed = await retryRequestRepo.claimPending(safeLimit, safeLease, claim_engine, Math.floor(Date.now() / 1000), safeFlowIds, options_version === 1);
      apiLog("WRITE", "/retry-requests/claim", { status: 200, claimEngine: claim_engine, claimed: claimed.length });
      res.json({ success: true, data: { claimed } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/retry-requests/claim", { status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:requestId/complete — mark a claimed request finished */
  router.post("/:requestId/complete", asyncHandler(async (req: Request, res: Response) => {
    const requestId = String(req.params.requestId);
    apiLogBody("WRITE", `/retry-requests/${requestId}/complete`, req.body, { requestId });
    if (!retryRequestRepo) {
      apiLog("WRITE", `/retry-requests/${requestId}/complete`, { requestId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { ok, message } = req.body as { ok?: boolean; message?: string };
      if (typeof ok !== "boolean") {
        apiLog("WRITE", `/retry-requests/${requestId}/complete`, { requestId, status: 400, error: "Missing or invalid ok" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: ok (boolean)" });
        return;
      }
      const done = await retryRequestRepo.complete(requestId, ok, message ?? "");
      if (!done) {
        apiLog("WRITE", `/retry-requests/${requestId}/complete`, { requestId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Retry request "${requestId}" not found` });
        return;
      }
      apiLog("WRITE", `/retry-requests/${requestId}/complete`, { requestId, status: 200, ok });
      res.json({ success: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", `/retry-requests/${requestId}/complete`, { requestId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:requestId/release — hand a claimed request back to the pool */
  router.post("/:requestId/release", asyncHandler(async (req: Request, res: Response) => {
    const requestId = String(req.params.requestId);
    apiLog("WRITE", `/retry-requests/${requestId}/release`, { requestId });
    if (!retryRequestRepo) {
      apiLog("WRITE", `/retry-requests/${requestId}/release`, { requestId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const done = await retryRequestRepo.release(requestId);
      if (!done) {
        apiLog("WRITE", `/retry-requests/${requestId}/release`, { requestId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Retry request "${requestId}" not found` });
        return;
      }
      apiLog("WRITE", `/retry-requests/${requestId}/release`, { requestId, status: 200 });
      res.json({ success: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", `/retry-requests/${requestId}/release`, { requestId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}
