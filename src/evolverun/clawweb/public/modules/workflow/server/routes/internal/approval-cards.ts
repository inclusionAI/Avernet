/**
 * Internal API routes for approval_cards — write operations for workflow runtime.
 *
 * Endpoints:
 *   POST /             — insert a new approval card
 *   GET  /resolved     — find resolved card-web cards for workflow runtime to process
 *   POST  /:id/processed — mark a card as processed
 */
import { Router, type Request, type Response } from "express";
import { ApprovalCardRepository } from "../../repositories/approval-card-repository.js";
import { apiLog, apiLogBody } from "../internal-logger.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

// ── Resolved-cards cache ───────────────────────────────────────────────
// workflow runtime's card-web-poller hits GET /resolved at high frequency.
// Since the result set changes infrequently (only when an approver acts),
// a short TTL cache dramatically reduces DB load without meaningful
// latency impact — the poller already runs on 60s+ intervals.

const RESOLVED_CACHE_TTL_MS = 30_000; // 30 seconds

type CachedResult = { data: unknown; timestamp: number };

let _resolvedCache: CachedResult | null = null;

/** Invalidate the resolved-cards cache (call on writes that change the set). */
export function invalidateResolvedCache(): void {
  _resolvedCache = null;
}

/** Return cached data if fresh, otherwise null. */
function getResolvedCache(): unknown | null {
  if (!_resolvedCache) return null;
  if (Date.now() - _resolvedCache.timestamp > RESOLVED_CACHE_TTL_MS) {
    _resolvedCache = null;
    return null;
  }
  return _resolvedCache.data;
}

export function createInternalApprovalCardsRouter(approvalCardRepo: ApprovalCardRepository | null): Router {
  const router = Router();

  /** POST / — insert a new approval card */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    apiLogBody("WRITE", "/approval-cards", req.body);
    if (!approvalCardRepo) {
      apiLog("WRITE", "/approval-cards", { status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    const {
        flow_id,
        node_id,
        workflow_id,
        workflow_title,
        approval_type,
        message,
        card_fields_json,
        approver_ids,
        approver_names,
        approval_policy,
        delivery_mode,
      } = req.body as {
        flow_id?: string;
        node_id?: string;
        workflow_id?: string;
        workflow_title?: string;
        approval_type?: string;
        message?: string;
        card_fields_json?: string;
        approver_ids?: string;
        approver_names?: string;
        approval_policy?: string;
        delivery_mode?: string;
        [key: string]: unknown;
      };

    try {
      if (!flow_id || !node_id || !workflow_id || !approver_ids) {
        apiLog("WRITE", "/approval-cards", { httpStatus: 400, error: "Missing required fields" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: flow_id, node_id, workflow_id, approver_ids" });
        return;
      }

      const id = await approvalCardRepo.create({
        flowId: flow_id,
        nodeId: node_id,
        workflowId: workflow_id,
        workflowTitle: workflow_title,
        approvalType: approval_type,
        message,
        cardFields: card_fields_json ? JSON.parse(card_fields_json) : undefined,
        approverIds: approver_ids.split(",").map((s: string) => s.trim()).filter(Boolean),
        approverNames: approver_names ? approver_names.split(",").map((s: string) => s.trim()).filter(Boolean) : undefined,
        approvalPolicy: approval_policy ?? "any",
        deliveryMode: delivery_mode ?? "card-web",
      });

      apiLog("WRITE", "/approval-cards", { httpStatus: 201, id });
      // New card may be resolved soon — invalidate cache so poller picks it up
      invalidateResolvedCache();
      res.status(201).json({ success: true, data: { id } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const code = (error as any)?.code;
      const sqlMessage = (error as any)?.sqlMessage;
      apiLog("WRITE", "/approval-cards", { httpStatus: 500, error: msg, code, sqlMessage });
      console.error(`[approval-cards] insert failed: ${msg}`, { code, sqlMessage, flow_id, node_id, workflow_id });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg, code, detail: sqlMessage });
    }
  }));

  /** GET /resolved — find resolved card-web cards for workflow runtime polling */
  router.get("/resolved", asyncHandler(async (_req: Request, res: Response) => {
    if (!approvalCardRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      // Serve from TTL cache if fresh — avoids hitting DB on every poll
      const cached = getResolvedCache();
      if (cached) {
        res.json(cached);
        return;
      }

      const limit = parseInt(_req.query.limit as string, 10) || 50;
      const cards = await approvalCardRepo.findResolvedCardWeb(limit);
      const body = { success: true, data: cards };
      _resolvedCache = { data: body, timestamp: Date.now() };
      res.json(body);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:id/processed — mark a card as processed */
  router.post("/:id/processed", asyncHandler(async (req: Request, res: Response) => {
    if (!approvalCardRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      const id = parseInt(String(req.params.id), 10);
      if (isNaN(id)) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Invalid id" });
        return;
      }
      await approvalCardRepo.markProcessed(id);
      res.json({ success: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}