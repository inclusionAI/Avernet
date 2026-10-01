/**
 * Flow control monitoring routes.
 * GET / — get flow control status (all scopes)
 * GET /queue — get queued items
 * GET /slots — get active slots
 */
import { Router, type Request, type Response } from "express";
import type { FlowControlService } from "../../flow-control/service.js";

export function createFlowControlRouter(
  service: FlowControlService | null,
): Router {
  const router = Router();

  // GET / — Flow control status across all scopes
  router.get("/", async (_req: Request, res: Response) => {
    if (!service) {
      res.status(503).json({ error: "Service Unavailable", message: "Flow control not enabled" });
      return;
    }
    try {
      const status = await service.getAllStatus();
      res.json({ data: status });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  // GET /queue — Queued items waiting for slots
  router.get("/queue", async (req: Request, res: Response) => {
    if (!service) {
      res.status(503).json({ error: "Service Unavailable", message: "Flow control not enabled" });
      return;
    }
    try {
      const scopeKey = req.query.scope as string | undefined;
      const limit = Math.min(parseInt(req.query.limit as string) || 100, 500);
      const items = await service.getQueueItems(scopeKey, limit);
      res.json({ data: items });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  // GET /slots — Active slots currently held
  router.get("/slots", async (req: Request, res: Response) => {
    if (!service) {
      res.status(503).json({ error: "Service Unavailable", message: "Flow control not enabled" });
      return;
    }
    try {
      const scopeKey = req.query.scope as string | undefined;
      const slots = await service.getSlots(scopeKey);
      res.json({ data: slots });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}