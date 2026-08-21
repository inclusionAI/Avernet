/**
 * Flow event query routes.
 * GET /:flowId/events — list events for a flow
 */
import { Router, type Request, type Response } from "express";
import type { IFlowEventRepository } from "../../db/repositories/types.js";

export function createEventsRouter(repo: IFlowEventRepository | null): Router {
  const router = Router();

  router.get("/:flowId/events", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rawLimit = req.query.limit as string | undefined;
      const limit = rawLimit ? Math.min(parseInt(rawLimit, 10), 1000) : 100;
      const events = await repo.findByFlowId(String(req.params.flowId), { limit });
      res.json({ data: events });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}