import { captureApiError } from "../error-log-format.js";
/**
 * Internal API routes for flow_events — write and read operations for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import { FlowEventRepository } from "@avernet/workflow/server/repositories/event-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { ResolveVerifiedAIStudioService } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
import { manualAuditIdentity, type AIStudioTrainManualAuditRepository } from '../../repositories/aistudio-train-manual-audit-repository.js';
import type { AIStudioRunTarget } from '../../repositories/aistudio-train-watch-repository.js';
import type { FlowEventInsert } from '../../repositories/event-repository.js';
import type { WatchRecord } from '../../contracts/aistudio-train.js';


export function createInternalEventsRouter(eventRepo: FlowEventRepository | null): Router {
  const router = Router();

  /** POST / — insert an event */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    const { event_id, flow_id, event_type } = req.body as { event_id?: string; flow_id?: string; event_type?: string };
    apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, eventType: event_type });
    if (!eventRepo) {
      apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, status: 503, error: "Database not configured" }, undefined, res);
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
      if (ok !== true) {
        apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, eventType: event_type, status: 503, error: "Event persistence not confirmed" }, undefined, res);
        res.status(503).json({ success: false, error: "Service Unavailable", message: "Event persistence not confirmed" });
        return;
      }
      apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, eventType: event_type, status: 201 });
      res.status(201).json({ success: true, data: { inserted: ok } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/events", { eventId: event_id, flowId: flow_id, status: 500, error: msg }, error, res);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — find by flowId */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    const flowId = req.query.flowId as string | undefined;
    apiLog("READ", "/events", { flowId });
    if (!eventRepo) {
      apiLog("READ", "/events", { flowId, status: 503, error: "Database not configured" }, undefined, res);
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
      apiLog("READ", "/events", { flowId, status: 500, error: msg }, error, res);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}
/** Dedicated AIStudio audit port. Mount at /api/internal/events, NOT through the
 * legacy writer or its authentication defaults. It exposes only this subpath. */
export function createInternalAIStudioTrainManualAuditRouter(deps: {
  repository: Pick<AIStudioTrainManualAuditRepository, 'isReady' | 'insertAIStudioTrainManualAudit'> | null;
  resolveVerifiedService?: ResolveVerifiedAIStudioService;
  findRunTarget(flowId: string): Promise<AIStudioRunTarget | null>;
  findWatch(flowId: string,nodeId: string,attempt: number): Promise<WatchRecord | null>;
}): Router {
  const router = Router();
  router.post('/aistudio-train-manual-audit',async (req,res) => {
    res.set('Cache-Control','no-store');
    const fail = (status: number,error: string) => { res.status(status).json({success:false,error}); };
    if (!deps.repository || !deps.resolveVerifiedService) { fail(503,'AIStudio audit service unavailable'); return; }
    try {
      if (!(await deps.resolveVerifiedService(req))?.serviceId?.trim()) { fail(401,'Service authentication required'); return; }
      if (!await deps.repository.isReady()) { fail(503,'AIStudio audit persistence unavailable'); return; }
      let event: FlowEventInsert;
      try {
        const body = req.body;
        const required = ['event_id','flow_id','workflow_id','node_id','event_type','attempt','time','data_json'];
        const allowed = [...required,'action_id','error_text'];
        if (!body || typeof body !== 'object' || Array.isArray(body) || required.some(key => !Object.hasOwn(body,key))
          || Object.keys(body).some(key => !allowed.includes(key)) || typeof body.data_json !== 'string'
          || body.data_json.length > 8192 || (body.error_text != null && typeof body.error_text !== 'string')
          || (body.action_id != null && (typeof body.action_id !== 'string' || body.action_id.length > 255))) throw new Error('Invalid manual audit body');
        event = {id:body.event_id,flowId:body.flow_id,workflowId:body.workflow_id,nodeId:body.node_id,type:body.event_type,
          attempt:body.attempt,time:body.time,data:JSON.parse(body.data_json),error:body.error_text ?? null,actionId:body.action_id ?? null};
        manualAuditIdentity(event);
      } catch { fail(400,'Invalid AIStudio manual audit'); return; }
      const run = await deps.findRunTarget(event.flowId);
      const watch = await deps.findWatch(event.flowId,event.nodeId!,event.attempt!);
      if (!run || !watch || run.flowId !== event.flowId || run.workflowId !== event.workflowId
        || watch.flowId !== event.flowId || watch.workflowId !== event.workflowId || watch.nodeId !== event.nodeId
        || watch.attempt !== event.attempt || run.ownerBotId !== watch.ownerBotId || watch.watchId !== event.data!.watchId) {
        fail(403,'AIStudio audit target forbidden'); return;
      }
      if (await deps.repository.insertAIStudioTrainManualAudit(event) !== true) { fail(503,'AIStudio audit persistence unconfirmed'); return; }
      res.status(201).json({success:true,data:{inserted:true}});
    } catch (error) { console.error("[internal/events] request failed:", captureApiError(res, error)); fail(503,'AIStudio audit service unavailable'); }
  });
  return router;
}
