/**
 * Workflow node stats API routes.
 *
 * GET /api/workflows/:workflowId/node-stats — per-node statistics
 * GET /api/workflows/:workflowId/health   — workflow health score
 */
import { Router, type Request, type Response } from "express";
import type { NodeStatsRepository } from "../repositories/node-stats-repository.js";
import type { HealthSnapshotRepository } from "../repositories/health-snapshot-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createWorkflowNodeStatsRouter(
  nodeStatsRepo: NodeStatsRepository | null,
  healthSnapshotRepo: HealthSnapshotRepository | null,
): Router {
  const router = Router();

  // GET /:workflowId/node-stats — per-node statistics
  router.get("/:workflowId/node-stats", asyncHandler(async (req: Request, res: Response) => {
    if (!nodeStatsRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Node stats repository not configured" });
      return;
    }
    const workflowId = String(req.params.workflowId);
    const days = req.query.days ? parseInt(String(req.query.days), 10) : undefined;
    const stats = await nodeStatsRepo.getNodeStats(workflowId, days);
    res.json({ data: stats });
  }));

  // GET /:workflowId/health — workflow health score
  router.get("/:workflowId/health", asyncHandler(async (req: Request, res: Response) => {
    if (!nodeStatsRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Node stats repository not configured" });
      return;
    }
    const workflowId = String(req.params.workflowId);
    const days = req.query.days ? parseInt(String(req.query.days), 10) : undefined;
    const health = await nodeStatsRepo.getWorkflowHealth(workflowId, days);
    res.json({ data: health });
  }));

  // GET /:workflowId/node-stats/error-breakdown?nodeId=xxx — detailed error breakdown for a node
  router.get("/:workflowId/node-stats/error-breakdown", asyncHandler(async (req: Request, res: Response) => {
    if (!nodeStatsRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Node stats repository not configured" });
      return;
    }
    const workflowId = String(req.params.workflowId);
    const nodeId = String(req.query.nodeId ?? "");
    const days = req.query.days ? parseInt(String(req.query.days), 10) : undefined;
    if (!nodeId) {
      res.status(400).json({ error: "Bad Request", message: "Missing required parameter: nodeId" });
      return;
    }
    const breakdown = await nodeStatsRepo.getErrorBreakdown(workflowId, nodeId, days);
    res.json({ data: breakdown });
  }));

  // GET /:workflowId/success-trend?days=7 — daily success rate trend
  router.get("/:workflowId/success-trend", asyncHandler(async (req: Request, res: Response) => {
    if (!nodeStatsRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Node stats repository not configured" });
      return;
    }
    const days = req.query.days ? parseInt(String(req.query.days), 10) : 7;
    const trend = await nodeStatsRepo.getSuccessTrend(String(req.params.workflowId), days);
    res.json({ data: trend });
  }));

  // GET /:workflowId/health-trend?days=7 — historical health score trend
  router.get("/:workflowId/health-trend", asyncHandler(async (req: Request, res: Response) => {
    const days = req.query.days ? parseInt(String(req.query.days), 10) : 7;
    if (!healthSnapshotRepo) {
      res.json({ data: [] });
      return;
    }
    const snapshots = await healthSnapshotRepo.findByWorkflowAndDays(String(req.params.workflowId), days);
    res.json({ data: snapshots });
  }));

  return router;
}