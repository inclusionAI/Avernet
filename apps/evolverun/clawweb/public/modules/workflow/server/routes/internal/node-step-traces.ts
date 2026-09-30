/**
 * Internal API routes for node_step_traces — write and read operations for ClawMind.
 * Mounted at /api/internal/node-step-traces
 */
import { Router, type Request, type Response } from "express";
import { NodeStepTraceRepository } from "@avernet/workflow/server/repositories/node-step-traces-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalNodeStepTracesRouter(
  nodeStepTraceRepo: NodeStepTraceRepository | null,
): Router {
  const router = Router();

  /** POST /batch — batch insert step traces */
  router.post("/batch", asyncHandler(async (req: Request, res: Response) => {
    const { steps } = req.body as {
      steps?: Array<{
        flow_id: string;
        node_id: string;
        attempt: number;
        step_seq: number;
        step_type: string;
        skill_name?: string | null;
        tool_name?: string | null;
        tool_use_id?: string | null;
        tool_input_json?: string | null;
        tool_output_text?: string | null;
        is_error?: number;
        text_content?: string | null;
        session_key?: string | null;
        trace_id?: string | null;
        observation_id?: string | null;
        model?: string | null;
        latency_ms?: number | null;
        prompt_tokens?: number | null;
        completion_tokens?: number | null;
      }>;
    };

    if (!nodeStepTraceRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!Array.isArray(steps) || steps.length === 0) {
      res
        .status(400)
        .json({ success: false, error: "Bad Request", message: "Missing or empty steps array" });
      return;
    }

    try {
      const inserts = steps.map((s) => ({
        flowId: s.flow_id,
        nodeId: s.node_id,
        attempt: s.attempt,
        stepSeq: s.step_seq,
        stepType: s.step_type,
        skillName: s.skill_name ?? null,
        toolName: s.tool_name ?? null,
        toolUseId: s.tool_use_id ?? null,
        toolInputJson: s.tool_input_json ?? null,
        toolOutputText: s.tool_output_text ?? null,
        isError: s.is_error ?? 0,
        textContent: s.text_content ?? null,
        sessionKey: s.session_key ?? null,
        traceId: s.trace_id ?? null,
        observationId: s.observation_id ?? null,
        modelVal: s.model ?? null,
        latencyMs: s.latency_ms ?? null,
        promptTokens: s.prompt_tokens ?? null,
        completionTokens: s.completion_tokens ?? null,
      }));

      const inserted = await nodeStepTraceRepo.insertBatch(inserts);
      apiLog("WRITE", "/node-step-traces/batch", {
        flowId: steps[0]?.flow_id,
        nodeId: steps[0]?.node_id,
        count: steps.length,
        inserted,
      });
      res.status(201).json({ success: true, data: { inserted } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const partialInserted = (error as Error & { inserted?: number })?.inserted ?? 0;
      apiLog("WRITE", "/node-step-traces/batch", { error: msg, partialInserted, status: 500 });
      res.status(500).json({
        success: false,
        error: "Internal Server Error",
        message: msg,
        inserted: partialInserted,
      });
    }
  }));

  /** POST / — insert a single step trace (for progress steps during execution) */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    const s = req.body as {
      flow_id: string;
      node_id: string;
      attempt: number;
      step_seq: number;
      step_type: string;
      skill_name?: string | null;
      tool_name?: string | null;
      tool_use_id?: string | null;
      tool_input_json?: string | null;
      tool_output_text?: string | null;
      is_error?: number;
      text_content?: string | null;
      session_key?: string | null;
      trace_id?: string | null;
      observation_id?: string | null;
      model?: string | null;
      latency_ms?: number | null;
      prompt_tokens?: number | null;
      completion_tokens?: number | null;
    };

    if (!nodeStepTraceRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    if (!s?.flow_id || !s?.node_id) {
      res
        .status(400)
        .json({ success: false, error: "Bad Request", message: "Missing flow_id or node_id" });
      return;
    }

    try {
      const inserted = await nodeStepTraceRepo.insert({
        flowId: s.flow_id,
        nodeId: s.node_id,
        attempt: s.attempt,
        stepSeq: s.step_seq,
        stepType: s.step_type,
        skillName: s.skill_name ?? null,
        toolName: s.tool_name ?? null,
        toolUseId: s.tool_use_id ?? null,
        toolInputJson: s.tool_input_json ?? null,
        toolOutputText: s.tool_output_text ?? null,
        isError: s.is_error ?? 0,
        textContent: s.text_content ?? null,
        sessionKey: s.session_key ?? null,
        traceId: s.trace_id ?? null,
        observationId: s.observation_id ?? null,
        modelVal: s.model ?? null,
        latencyMs: s.latency_ms ?? null,
        promptTokens: s.prompt_tokens ?? null,
        completionTokens: s.completion_tokens ?? null,
      });
      apiLog("WRITE", "/node-step-traces", { flowId: s.flow_id, nodeId: s.node_id, stepType: s.step_type, inserted });
      res.status(201).json({ success: true, data: { inserted } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/node-step-traces", { error: msg, status: 500 });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — find by flowId + nodeId + attempt */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    const flowId = req.query.flowId as string | undefined;
    const nodeId = req.query.nodeId as string | undefined;
    const attempt = parseInt(req.query.attempt as string, 10) || 1;

    if (!nodeStepTraceRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      if (!flowId || !nodeId) {
        res.status(400).json({
          success: false,
          error: "Bad Request",
          message: "Missing required query parameters: flowId, nodeId",
        });
        return;
      }

      const rows = await nodeStepTraceRepo.findByFlowNode(flowId, nodeId, attempt);
      apiLog("READ", "/node-step-traces", { flowId, nodeId, attempt, count: rows.length });
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/node-step-traces", { flowId, nodeId, error: msg, status: 500 });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /by-seq — find a single step by flowId + nodeId + attempt + stepSeq */
  router.get("/by-seq", asyncHandler(async (req: Request, res: Response) => {
    const flowId = req.query.flowId as string | undefined;
    const nodeId = req.query.nodeId as string | undefined;
    const attempt = parseInt(req.query.attempt as string, 10);
    const stepSeq = parseInt(req.query.stepSeq as string, 10);

    if (!nodeStepTraceRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      if (!flowId || !nodeId || Number.isNaN(attempt) || Number.isNaN(stepSeq)) {
        res.status(400).json({
          success: false,
          error: "Bad Request",
          message: "Missing required query parameters: flowId, nodeId, attempt, stepSeq",
        });
        return;
      }

      const row = await nodeStepTraceRepo.findBySeq(flowId, nodeId, attempt, stepSeq);
      if (!row) {
        res.status(404).json({
          success: false,
          error: "Not Found",
          message: `Step trace ${flowId}/${nodeId}/${attempt}/${stepSeq} not found`,
        });
        return;
      }
      apiLog("READ", "/node-step-traces/by-seq", { flowId, nodeId, attempt, stepSeq });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/node-step-traces/by-seq", { error: msg, status: 500 });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /summary — aggregate summary by flowId */
  router.get("/summary", asyncHandler(async (req: Request, res: Response) => {
    const flowId = req.query.flowId as string | undefined;

    if (!nodeStepTraceRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      if (!flowId) {
        res.status(400).json({
          success: false,
          error: "Bad Request",
          message: "Missing required query parameter: flowId",
        });
        return;
      }

      const rows = await nodeStepTraceRepo.findSummaryByFlowId(flowId);
      apiLog("READ", "/node-step-traces/summary", { flowId, count: rows.length });
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/node-step-traces/summary", { flowId, error: msg, status: 500 });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /delete — delete step traces by flowId */
  router.post("/delete", asyncHandler(async (req: Request, res: Response) => {
    const { flow_id } = req.body as { flow_id?: string };

    if (!nodeStepTraceRepo) {
      res
        .status(503)
        .json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      if (!flow_id) {
        res.status(400).json({
          success: false,
          error: "Bad Request",
          message: "Missing required field: flow_id",
        });
        return;
      }

      const deleted = await nodeStepTraceRepo.deleteByFlowId(flow_id);
      apiLog("WRITE", "/node-step-traces/delete", { flowId: flow_id, deleted });
      res.json({ success: true, data: { deleted } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/node-step-traces/delete", { error: msg, status: 500 });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}
