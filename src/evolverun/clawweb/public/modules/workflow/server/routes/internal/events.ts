/**
 * Internal API routes for flow_events — write and read operations for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import { FlowEventRepository } from "@avernet/workflow/server/repositories/event-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalEventsRouter(eventRepo: FlowEventRepository | null): Router {
  const router = Router();

  /** POST / — insert an event */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    const { event_id, flow_id, event_type } = req.body as { event_id?: string; flow_id?: string; event_type?: string };
    apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, eventType: event_type });
    if (!eventRepo) {
      apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { workflow_id, node_id, attempt, time, data_json, error_text } = req.body as {
        event_id?: string;
        flow_id?: string;
        workflow_id?: string;
        node_id?: string;
        event_type?: string;
        attempt?: number;
        time?: number;
        data_json?: string;
        error_text?: string;
      };

      if (!event_id || !flow_id || !workflow_id || !event_type) {
        apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, status: 400, error: "Missing required fields" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: event_id, flow_id, workflow_id, event_type" });
        return;
      }

      // Normalize timestamp: ClawMind sends milliseconds (Date.now()),
      // but the DB stores seconds. Accept both and always store as seconds.
      let normalizedTime: number;
      if (time !== undefined && time !== null) {
        normalizedTime = time > 1e12 ? Math.floor(time / 1000) : time;
      } else {
        normalizedTime = Math.floor(Date.now() / 1000);
      }

      const ok = await eventRepo.insert({
        id: event_id,
        flowId: flow_id,
        workflowId: workflow_id,
        nodeId: node_id ?? null,
        type: event_type,
        attempt: attempt ?? undefined,
        time: normalizedTime,
        data: data_json ? JSON.parse(data_json) : undefined,
        error: error_text ?? null,
      });
      apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, eventType: event_type, status: 201 });
      res.status(201).json({ success: true, data: { inserted: ok } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — find by flowId */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    const flowId = req.query.flowId as string | undefined;
    apiLog("READ", "/events", { flowId });
    if (!eventRepo) {
      apiLog("READ", "/events", { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (!flowId) {
        apiLog("READ", "/events", { status: 400, error: "Missing flowId" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required query parameter: flowId" });
        return;
      }

      const limit = Math.min(parseInt(req.query.limit as string, 10) || 200, 1000);
      const offset = parseInt(req.query.offset as string, 10) || 0;

      const rows = await eventRepo.findByFlowId(flowId, { limit, offset });
      apiLog("READ", "/events", { flowId, status: 200, count: rows.length });
      res.json({ success: true, data: rows, total: rows.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/events", { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}