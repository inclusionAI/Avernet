/**
 * Node execution query routes.
 * GET /:flowId/nodes — list node executions for a flow
 */
import { Router, type Request, type Response } from "express";
import type { INodeExecutionRepository } from "../../db/repositories/types.js";

export function createNodesRouter(repo: INodeExecutionRepository | null): Router {
  const router = Router();

  router.get("/:flowId/nodes", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rawLimit = req.query.limit as string | undefined;
      const limit = rawLimit ? Math.min(parseInt(rawLimit, 10), 1000) : 100;
      const nodes = await repo.findByFlowId(String(req.params.flowId), { limit });
      res.json({ data: nodes });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}