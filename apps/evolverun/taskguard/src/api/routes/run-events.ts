/**
 * Run events API route.
 * GET /:flowId/events — list flow events for a run
 */
import { Router, type Request, type Response } from "express";
import type { IFlowEventRepository } from "../../db/repositories/types.js";

export function createRunEventsRouter(repo: IFlowEventRepository | null): Router {
  const router = Router();

  router.get("/:flowId/events", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const limit = Math.min(parseInt(req.query.limit as string, 10) || 200, 1000);
      const events = await repo.findByFlowId(flowId, { limit });
      res.json(events);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}