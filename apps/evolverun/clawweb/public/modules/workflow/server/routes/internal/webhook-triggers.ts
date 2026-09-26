/**
 * Internal API routes for webhook_triggers — full CRUD for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import type { WebhookTriggerRepository } from "../../repositories/webhook-trigger-repository.js";

type RouterDeps = { webhookTriggerRepo: WebhookTriggerRepository | null };
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalWebhookTriggersRouter(repos: RouterDeps): Router {
  const router = Router();

  /** POST / — create a webhook trigger */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { trigger_id, workflow_id, pack_id, secret, payload_mapping, allowed_ips, enabled, description } = req.body as {
        trigger_id?: string;
        workflow_id?: string;
        pack_id?: string;
        secret?: string;
        payload_mapping?: string;
        allowed_ips?: string;
        enabled?: number;
        description?: string;
      };

      if (!trigger_id || !workflow_id) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: trigger_id, workflow_id" });
        return;
      }

      const row = await repos.webhookTriggerRepo.insert({
        trigger_id,
        workflow_id,
        pack_id: pack_id ?? null,
        secret: secret ?? null,
        payload_mapping: payload_mapping ?? null,
        allowed_ips: allowed_ips ?? null,
        enabled,
        description: description ?? null,
      });
      res.status(201).json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — list webhook triggers */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const enabled = req.query.enabled !== undefined ? parseInt(req.query.enabled as string, 10) : undefined;
      const limit = Math.min(parseInt(req.query.limit as string, 10) || 100, 500);
      const offset = parseInt(req.query.offset as string, 10) || 0;

      const rows = await repos.webhookTriggerRepo.listAll({ enabled, limit, offset });
      res.json({ success: true, data: rows, total: rows.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:triggerId — get a webhook trigger */
  router.get("/:triggerId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const row = await repos.webhookTriggerRepo.findByTriggerId(String(req.params.triggerId));
      if (!row) {
        res.status(404).json({ success: false, error: "Not Found", message: `Webhook trigger "${req.params.triggerId}" not found` });
        return;
      }
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /workflow/:workflowId — find by workflow id */
  router.get("/workflow/:workflowId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rows = await repos.webhookTriggerRepo.findByWorkflowId(String(req.params.workflowId));
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:triggerId — update a webhook trigger */
  router.put("/:triggerId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { workflow_id, pack_id, secret, payload_mapping, allowed_ips, enabled, description } = req.body as {
        workflow_id?: string;
        pack_id?: string;
        secret?: string;
        payload_mapping?: string;
        allowed_ips?: string;
        enabled?: number;
        description?: string;
      };

      const row = await repos.webhookTriggerRepo.update(String(req.params.triggerId), {
        workflow_id,
        pack_id: pack_id ?? undefined,
        secret: secret ?? undefined,
        payload_mapping: payload_mapping ?? undefined,
        allowed_ips: allowed_ips ?? undefined,
        enabled,
        description: description ?? undefined,
      });
      if (!row) {
        res.status(404).json({ success: false, error: "Not Found", message: `Webhook trigger "${req.params.triggerId}" not found` });
        return;
      }
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:triggerId — delete a webhook trigger */
  router.delete("/:triggerId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.webhookTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const deleted = await repos.webhookTriggerRepo.delete(String(req.params.triggerId));
      if (!deleted) {
        res.status(404).json({ success: false, error: "Not Found", message: `Webhook trigger "${req.params.triggerId}" not found` });
        return;
      }
      res.json({ success: true, data: { affected: true } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}