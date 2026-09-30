/**
 * Internal API routes for flow_metrics — write and aggregate operations for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import { FlowMetricsRepository } from "@avernet/workflow/server/repositories/metrics-repository";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalMetricsRouter(metricsRepo: FlowMetricsRepository | null): Router {
  const router = Router();

  /** POST / — record a metric */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!metricsRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { flow_id, workflow_id, node_id, metric_name, metric_value, time, labels_json } = req.body as {
        flow_id?: string;
        workflow_id?: string;
        node_id?: string;
        metric_name?: string;
        metric_value?: number;
        time?: number;
        labels_json?: string;
      };

      if (!flow_id || !workflow_id || !node_id || !metric_name || metric_value === undefined) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: flow_id, workflow_id, node_id, metric_name, metric_value" });
        return;
      }

      const row = await metricsRepo.insert({
        flow_id,
        workflow_id,
        node_id,
        metric_name,
        metric_value,
        time,
        labels_json: labels_json ?? null,
      });
      res.status(201).json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /aggregate — aggregate metrics */
  router.get("/aggregate", asyncHandler(async (req: Request, res: Response) => {
    if (!metricsRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const workflow_id = req.query.workflow_id as string | undefined;
      const metric_name = req.query.metric_name as string | undefined;
      const start_time = req.query.start_time ? parseInt(req.query.start_time as string, 10) : undefined;
      const end_time = req.query.end_time ? parseInt(req.query.end_time as string, 10) : undefined;

      const rows = await metricsRepo.aggregate({ workflow_id, metric_name, start_time, end_time });
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — find metrics by flowId */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    if (!metricsRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = req.query.flowId as string | undefined;

      if (!flowId) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required query parameter: flowId" });
        return;
      }

      const rows = await metricsRepo.findByFlowId(flowId);
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}