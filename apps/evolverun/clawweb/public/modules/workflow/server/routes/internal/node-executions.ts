/**
 * Internal API routes for node_executions — write and read operations for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import { NodeExecutionRepository } from "@avernet/workflow/server/repositories/node-execution-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalNodeExecutionsRouter(nodeExecRepo: NodeExecutionRepository | null): Router {
  const router = Router();

  /** POST / — insert a node execution */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    const { flow_id, node_id, status } = req.body as { flow_id?: string; node_id?: string; status?: string };
    apiLog("WRITE", "/node-executions", { flowId: flow_id, nodeId: node_id, status });
    if (!nodeExecRepo) {
      apiLog("WRITE", "/node-executions", { flowId: flow_id, nodeId: node_id, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const {
        workflow_id,
        executor_type,
        node_title,
        session_key,
        session_id,
        embedded_session_key,
        attempt,
        input_json,
        output_json,
        error_text,
        duration_ms,
        token_usage_json,
        progress_message,
        system_context_json,
        resolved_prompt,
        started_at,
        completed_at,
        version,
      } = req.body as {
        flow_id?: string;
        workflow_id?: string;
        node_id?: string;
        executor_type?: string;
        node_title?: string;
        session_key?: string;
        session_id?: string;
        embedded_session_key?: string;
        status?: string;
        attempt?: number;
        input_json?: string | null;
        output_json?: string | null;
        error_text?: string | null;
        duration_ms?: number | null;
        token_usage_json?: string | null;
        progress_message?: string | null;
        system_context_json?: string | null;
        resolved_prompt?: string | null;
        started_at?: number;
        completed_at?: number | null;
        version?: number;
      };

      if (!flow_id || !workflow_id || !node_id || !status) {
        apiLog("WRITE", "/node-executions", { flowId: flow_id, nodeId: node_id, status: 400, error: "Missing required fields" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: flow_id, workflow_id, node_id, status" });
        return;
      }

      const result = await nodeExecRepo.insert({
        flowId: flow_id,
        workflowId: workflow_id,
        nodeId: node_id,
        executorType: executor_type ?? null,
        nodeTitle: node_title ?? null,
        sessionKey: session_key ?? null,
        sessionId: session_id ?? null,
        embeddedSessionKey: embedded_session_key ?? null,
        status,
        attempt: attempt ?? 1,
        inputJson: input_json ?? null,
        outputJson: output_json ?? null,
        errorText: error_text ?? null,
        durationMs: duration_ms ?? null,
        tokenUsageJson: token_usage_json ?? null,
        progressMessage: progress_message ?? null,
        systemContextJson: system_context_json ?? null,
        resolvedPrompt: resolved_prompt ?? null,
        version: version ?? 1,
        startedAt: started_at ?? Math.floor(Date.now() / 1000),
        completedAt: completed_at ?? null,
      });
      if (result < 0) {
        apiLog("WRITE", "/node-executions", { flowId: flow_id, nodeId: node_id, status: 500, error: "Failed to insert node execution" });
        res.status(500).json({ success: false, error: "Internal Server Error", message: "Failed to insert node execution" });
        return;
      }
      apiLog("WRITE", "/node-executions", { flowId: flow_id, nodeId: node_id, status: 201, insertId: result });
      res.status(201).json({ success: true, data: { insertId: result, affectedRows: 1 } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/node-executions", { flowId: flow_id, nodeId: node_id, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/:nodeId/:attempt/completion — update by flow+node+attempt */
  router.put("/:flowId/:nodeId/:attempt/completion", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const nodeId = String(req.params.nodeId);
    const attempt = String(req.params.attempt);
    const { status } = req.body as { status?: string };
    apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, attempt, newStatus: status });
    if (!nodeExecRepo) {
      apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const attemptNum = parseInt(String(attempt), 10);

      if (Number.isNaN(attemptNum)) {
        apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, status: 400, error: "Invalid attempt" });
        res.status(400).json({ success: false, error: "Bad Request", message: "attempt must be a number" });
        return;
      }

      const { output_json, error_text, duration_ms, token_usage_json, embedded_session_key, system_context_json, resolved_prompt, completed_at, expected_version } = req.body as {
        status?: string;
        output_json?: string;
        error_text?: string;
        duration_ms?: number;
        token_usage_json?: string;
        embedded_session_key?: string;
        system_context_json?: string;
        resolved_prompt?: string;
        completed_at?: number;
        expected_version?: number;
      };

      const row = await nodeExecRepo.updateCompletionByFlowNode(flowId, nodeId, attemptNum, {
        status: status!,
        outputJson: output_json ?? undefined,
        errorText: error_text ?? undefined,
        durationMs: duration_ms,
        tokenUsageJson: token_usage_json ?? undefined,
        embeddedSessionKey: embedded_session_key ?? undefined,
        systemContextJson: system_context_json ?? undefined,
        resolvedPrompt: resolved_prompt ?? undefined,
        completedAt: completed_at ?? Math.floor(Date.now() / 1000),
        expectedVersion: expected_version,
      });
      if (!row) {
        if (expected_version !== undefined) {
          // Optimistic lock conflict — return 409 so the API client can
          // distinguish "conflict" from "not found".
          apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, status: 409, error: "Optimistic lock conflict" });
          res.status(409).json({ success: false, error: "Conflict", message: `Node execution ${flowId}/${nodeId}/${attemptNum} version mismatch` });
          return;
        }
        apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Node execution ${flowId}/${nodeId}/${attemptNum} not found` });
        return;
      }
      apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, status: 200, newStatus: status, hasError: !!error_text });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/completion`, { flowId, nodeId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:id/completion — update by id */
  router.put("/:id/completion", asyncHandler(async (req: Request, res: Response) => {
    const id = String(req.params.id);
    const { status } = req.body as { status?: string };
    apiLog("PUT", `/node-executions/${id}/completion`, { id, newStatus: status });
    if (!nodeExecRepo) {
      apiLog("PUT", `/node-executions/${id}/completion`, { id, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const numId = parseInt(String(id), 10);

      if (Number.isNaN(numId)) {
        apiLog("PUT", `/node-executions/${id}/completion`, { id, status: 400, error: "Invalid id" });
        res.status(400).json({ success: false, error: "Bad Request", message: "id must be a number" });
        return;
      }

      const { output_json, error_text, duration_ms, token_usage_json, embedded_session_key, system_context_json, resolved_prompt, completed_at } = req.body as {
        status?: string;
        output_json?: string;
        error_text?: string;
        duration_ms?: number;
        token_usage_json?: string;
        embedded_session_key?: string;
        system_context_json?: string;
        resolved_prompt?: string;
        completed_at?: number;
      };

      const row = await nodeExecRepo.updateCompletion(numId, {
        status: status!,
        outputJson: output_json ?? undefined,
        errorText: error_text ?? undefined,
        durationMs: duration_ms,
        tokenUsageJson: token_usage_json ?? undefined,
        embeddedSessionKey: embedded_session_key ?? undefined,
        systemContextJson: system_context_json ?? undefined,
        resolvedPrompt: resolved_prompt ?? undefined,
        completedAt: completed_at ?? Math.floor(Date.now() / 1000),
      });
      if (!row) {
        apiLog("PUT", `/node-executions/${id}/completion`, { id, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Node execution #${id} not found` });
        return;
      }
      apiLog("PUT", `/node-executions/${id}/completion`, { id, status: 200, newStatus: status });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/node-executions/${id}/completion`, { id, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/:nodeId/:attempt/progress — update progress message */
  router.put("/:flowId/:nodeId/:attempt/progress", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const nodeId = String(req.params.nodeId);
    const attempt = String(req.params.attempt);
    const { message } = req.body as { message?: string };
    apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, attempt, message: message?.substring(0, 100) });
    if (!nodeExecRepo) {
      apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const attemptNum = parseInt(String(attempt), 10);

      if (Number.isNaN(attemptNum)) {
        apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, status: 400, error: "Invalid attempt" });
        res.status(400).json({ success: false, error: "Bad Request", message: "attempt must be a number" });
        return;
      }

      if (!message) {
        apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, status: 400, error: "Missing message" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: message" });
        return;
      }

      const row = await nodeExecRepo.updateProgressMessage(flowId, nodeId, attemptNum, message);
      if (!row) {
        apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Node execution ${flowId}/${nodeId}/${attemptNum} not found` });
        return;
      }
      apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, status: 200 });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/node-executions/${flowId}/${nodeId}/${attempt}/progress`, { flowId, nodeId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — find by flowId */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    const flowId = req.query.flowId as string | undefined;
    apiLog("READ", "/node-executions", { flowId });
    if (!nodeExecRepo) {
      apiLog("READ", "/node-executions", { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (!flowId) {
        apiLog("READ", "/node-executions", { status: 400, error: "Missing flowId" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required query parameter: flowId" });
        return;
      }

      const limit = Math.min(parseInt(req.query.limit as string, 10) || 500, 1000);
      const offset = parseInt(req.query.offset as string, 10) || 0;

      const rows = await nodeExecRepo.findByFlowId(flowId, { limit, offset });
      apiLog("READ", "/node-executions", { flowId, status: 200, count: rows.length });
      res.json({ success: true, data: rows, total: rows.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/node-executions", { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/reconcile-stale-running — reconcile stale "running" nodes when flow reaches terminal state */
  router.put("/:flowId/reconcile-stale-running", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const { flow_status } = req.body as { flow_status?: string };
    apiLog("PUT", `/node-executions/${flowId}/reconcile-stale-running`, { flowId, flow_status });
    if (!nodeExecRepo) {
      apiLog("PUT", `/node-executions/${flowId}/reconcile-stale-running`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (!flow_status) {
        apiLog("PUT", `/node-executions/${flowId}/reconcile-stale-running`, { flowId, status: 400, error: "Missing flow_status" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: flow_status" });
        return;
      }
      const reconciled = await nodeExecRepo.reconcileStaleRunning(flowId, flow_status);
      apiLog("PUT", `/node-executions/${flowId}/reconcile-stale-running`, { flowId, status: 200, reconciled });
      res.json({ success: true, data: { flowId, reconciled } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/node-executions/${flowId}/reconcile-stale-running`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}
