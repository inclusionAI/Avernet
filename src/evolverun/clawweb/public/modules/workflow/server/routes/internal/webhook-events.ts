/**
 * Internal API routes for webhook_events — record, dedup, cleanup for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import type { WebhookEventRepository } from "../../repositories/webhook-event-repository.js";

type RouterDeps = { webhookEventRepo: WebhookEventRepository | null };
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalWebhookEventsRouter(repos: RouterDeps): Router {
  const router = Router();

  /** POST / — record a webhook event */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookEventRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { event_id, trigger_id, flow_id, status, request_method, request_headers, request_body_hash, response_code, error_message, ip_address } = req.body as {
        event_id?: string;
        trigger_id?: string;
        flow_id?: string;
        status?: string;
        request_method?: string;
        request_headers?: string;
        request_body_hash?: string;
        response_code?: number;
        error_message?: string;
        ip_address?: string;
      };

      if (!event_id || !trigger_id || !status || !request_method) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: event_id, trigger_id, status, request_method" });
        return;
      }

      const row = await repos.webhookEventRepo.insert({
        event_id,
        trigger_id,
        flow_id: flow_id ?? null,
        status,
        request_method,
        request_headers: request_headers ?? null,
        request_body_hash: request_body_hash ?? null,
        response_code,
        error_message: error_message ?? null,
        ip_address: ip_address ?? null,
      });
      res.status(201).json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /duplicate — find a duplicate webhook event */
  router.get("/duplicate", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookEventRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const eventId = req.query.event_id as string | undefined;
      const triggerId = req.query.trigger_id as string | undefined;
      const bodyHash = req.query.body_hash as string | undefined;

      if (!eventId || !triggerId || !bodyHash) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required query parameters: event_id, trigger_id, body_hash" });
        return;
      }

      const row = await repos.webhookEventRepo.findDuplicate(triggerId, eventId, bodyHash, 300);
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /older-than — delete webhook events older than N seconds */
  router.delete("/older-than", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookEventRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const seconds = parseInt(req.query.seconds as string, 10);

      if (Number.isNaN(seconds) || seconds <= 0) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Query parameter 'seconds' must be a positive number" });
        return;
      }

      const affectedRows = await repos.webhookEventRepo.deleteOlderThan(seconds);
      res.json({ success: true, data: { deleted: affectedRows } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — find webhook events by trigger_id */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookEventRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const triggerId = req.query.trigger_id as string | undefined;

      if (!triggerId) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required query parameter: trigger_id" });
        return;
      }

      const limit = Math.min(parseInt(req.query.limit as string, 10) || 50, 200);
      const offset = parseInt(req.query.offset as string, 10) || 0;

      const rows = await repos.webhookEventRepo.findByTriggerId(triggerId, { limit, offset });
      res.json({ success: true, data: rows, total: rows.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}