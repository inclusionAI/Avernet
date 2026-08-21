/**
 * Runs API routes — ClawWeb-specific endpoints for flow run listing and detail.
 * GET /           — list flow runs with pagination and filters
 * GET /:flowId    — get single flow run with node executions
 */
import { Router, type Request, type Response } from "express";
import type { IFlowRunRepository, INodeExecutionRepository } from "../../db/repositories/types.js";

export function createRunsRouter(
  flowRunRepo: IFlowRunRepository | null,
  nodeExecRepo: INodeExecutionRepository | null,
): Router {
  const router = Router();

  /** GET / — list flow runs with pagination and filters */
  router.get("/", async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const status = req.query.status as string | undefined;
      const workflowId = req.query.workflowId as string | undefined;
      const identityKey = req.query.identity_key as string | undefined;
      const currentPhase = req.query.current_phase as string | undefined;
      const limit = Math.min(parseInt(req.query.limit as string, 10) || 50, 200);
      const offset = parseInt(req.query.offset as string, 10) || 0;

      const runs = await flowRunRepo.findRuns({ status, workflowId, identityKey, currentPhase, limit, offset });
      res.json({ runs, total: runs.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** GET /:flowId — get single run with its node executions */
  router.get("/:flowId", async (req: Request, res: Response) => {
    if (!flowRunRepo || !nodeExecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const run = await flowRunRepo.findByFlowId(String(req.params.flowId));
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${req.params.flowId} not found` });
        return;
      }
      const nodes = await nodeExecRepo.findLatestByFlowId(run.flow_id);
      res.json({ run, nodes });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}