/**
 * Alert query routes.
 * GET / — list unacknowledged alerts
 * POST /:id/acknowledge — acknowledge an alert
 */
import { Router, type Request, type Response } from "express";
import type { ITriggeredAlertRepository } from "../../db/repositories/types.js";

export function createAlertsRouter(repo: ITriggeredAlertRepository | null): Router {
  const router = Router();

  router.get("/", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const workflowId = req.query.workflowId as string | undefined;
      const severity = req.query.severity as string | undefined;
      const rawLimit = req.query.limit as string | undefined;
      const limit = rawLimit ? Math.min(parseInt(rawLimit, 10), 1000) : 100;

      if (!workflowId) {
        res.status(400).json({ error: "Bad Request", message: "workflowId query parameter is required" });
        return;
      }

      const alerts = await repo.findUnacknowledged(workflowId, {
        severity,
        limit,
      });

      res.json({ data: alerts });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  router.post("/:id/acknowledge", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const id = parseInt(String(req.params.id), 10);
      if (isNaN(id)) {
        res.status(400).json({ error: "Bad Request", message: "Invalid alert ID" });
        return;
      }

      const ok = await repo.acknowledge(id);
      if (!ok) {
        res.status(404).json({ error: "Not Found", message: `Alert ${id} not found` });
        return;
      }

      res.json({ data: { id, acknowledged: true } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}