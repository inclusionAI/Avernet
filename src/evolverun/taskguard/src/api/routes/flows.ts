/**
 * Flow run query routes.
 * GET / — list flow runs
 * GET /:flowId — get a specific flow run
 */
import { Router, type Request, type Response } from "express";
import type { IFlowRunRepository } from "../../db/repositories/types.js";

export function createFlowsRouter(repo: IFlowRunRepository | null): Router {
  const router = Router();

  router.get("/", async (_req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const runs = await repo.findRuns({});
      res.json({ data: runs });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  router.get("/:flowId", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const run = await repo.findByFlowId(String(req.params.flowId));
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${req.params.flowId} not found` });
        return;
      }
      res.json({ data: run });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}