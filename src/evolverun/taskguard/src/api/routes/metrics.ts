/**
 * Metrics query routes.
 * GET / — aggregate metrics for a workflow
 */
import { Router, type Request, type Response } from "express";
import type { IFlowMetricsRepository } from "../../db/repositories/types.js";

type AggregationType = "avg" | "count" | "sum";
const VALID_AGGS: AggregationType[] = ["avg", "count", "sum"];

export function createMetricsRouter(repo: IFlowMetricsRepository | null): Router {
  const router = Router();

  router.get("/", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const workflowId = req.query.workflowId as string | undefined;
      const metricName = (req.query.metric as string | undefined) ?? "node_duration_ms";
      const rawAgg = (req.query.aggregation as string | undefined) ?? "avg";
      const aggregation: AggregationType = VALID_AGGS.includes(rawAgg as AggregationType) ? (rawAgg as AggregationType) : "avg";
      const rawStart = req.query.startTime as string | undefined;
      const rawEnd = req.query.endTime as string | undefined;
      const startTime = rawStart ? parseInt(rawStart, 10) : Math.floor(Date.now() / 1000) - 86400;
      const endTime = rawEnd ? parseInt(rawEnd, 10) : Math.floor(Date.now() / 1000);

      if (!workflowId) {
        res.status(400).json({ error: "Bad Request", message: "workflowId query parameter is required" });
        return;
      }

      const results = await repo.aggregate(workflowId, startTime, endTime, {
        metricName,
        aggregation,
        groupBy: "node_id",
      });

      res.json({ data: results });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}