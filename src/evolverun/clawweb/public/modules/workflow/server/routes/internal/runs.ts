/**
 * Internal API routes for flow_runs — write and read operations for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import type { FlowControlAppConfig } from "@avernet/clawweb-shared/server/db";
import { apiLog, apiLogBody } from "../internal-logger.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { RunTerminalObserver } from "@avernet/clawevolve/server/services/evolve/run-analysis-starter";

function scheduleTerminalObserver(
  observer: RunTerminalObserver | undefined,
  input: { flowId: string; status: string },
): void {
  if (!observer) return;
  setImmediate(() => {
    void Promise.resolve()
      .then(() => observer(input))
      .catch((error) => {
        console.warn(`[internal-runs] terminal observer failed for ${input.flowId}: ${error instanceof Error ? error.message : String(error)}`);
      });
  });
}

export function createInternalRunsRouter(
  flowRunRepo: FlowRunRepository | null,
  nodeExecRepo?: NodeExecutionRepository | null,
  flowControlConfig?: FlowControlAppConfig,
  observers: { onTerminal?: RunTerminalObserver } = {},
): Router {
  const router = Router();

  /** POST / — insert a flow run */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    apiLogBody("WRITE", "/runs", req.body);
    if (!flowRunRepo) {
      apiLog("WRITE", "/runs", { status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { flow_id, workflow_id, workflow_title, status, triggered_by, params_json, input_json, node_count, identity_key, started_at, credentials_json, origin_session_key, origin_session_id, origin_bot_id, user_id, plugin_version, engine } = req.body as {
        flow_id?: string;
        workflow_id?: string;
        workflow_title?: string;
        status?: string;
        triggered_by?: string;
        params_json?: string;
        input_json?: string;
        node_count?: number;
        identity_key?: string;
        started_at?: number;
        credentials_json?: string;
        origin_session_key?: string;
        origin_session_id?: string;
        origin_bot_id?: string;
        user_id?: string;
        plugin_version?: string;
        engine?: string;
      };

      if (!flow_id || !workflow_id || !status) {
        apiLog("WRITE", "/runs", { httpStatus: 400, error: "Missing required fields", flow_id, workflow_id, runStatus: status });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: flow_id, workflow_id, status" });
        return;
      }

      const ok = await flowRunRepo.insert({
        flowId: flow_id,
        workflowId: workflow_id,
        workflowTitle: workflow_title ?? null,
        status,
        triggeredBy: triggered_by ?? null,
        paramsJson: params_json ?? null,
        inputJson: input_json ?? null,
        nodeCount: node_count ?? 0,
        identityKey: identity_key ?? null,
        startedAt: started_at ?? Math.floor(Date.now() / 1000),
        credentialsJson: credentials_json ?? null,
        originSessionKey: origin_session_key ?? null,
        originSessionId: origin_session_id ?? null,
        originBotId: origin_bot_id ?? null,
        userId: user_id ?? null,
        pluginVersion: plugin_version ?? null,
        engine: engine ?? null,
      });
      if (!ok) {
        apiLog("WRITE", "/runs", { status: 500, error: "Failed to insert flow run", flow_id, workflow_id });
        res.status(500).json({ success: false, error: "Internal Server Error", message: "Failed to insert flow run" });
        return;
      }
      const row = await flowRunRepo.findFullByFlowId(flow_id);
      apiLog("WRITE", "/runs", { status: 201, flow_id, workflow_id, status_field: status });
      res.status(201).json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/runs", { status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/completion — update completion fields */
  router.put("/:flowId/completion", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLogBody("PUT", `/runs/${flowId}/completion`, req.body, { flowId });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/completion`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { status, result_json, input_json, total_duration_ms, total_token_usage, succeeded_count, failed_count, completed_at } = req.body as {
        status?: string;
        result_json?: string;
        input_json?: string;
        total_duration_ms?: number;
        total_token_usage?: number;
        succeeded_count?: number;
        failed_count?: number;
        completed_at?: number;
      };

      const completion: import("../../repositories/flow-run-repository.js").FlowRunCompletion = {
        status: status!,
        totalDurationMs: total_duration_ms ?? null,
        totalTokenUsage: total_token_usage ?? null,
        completedAt: completed_at ?? Math.floor(Date.now() / 1000),
      };
      // result_json: only set when explicitly provided (undefined = preserve existing value)
      if (result_json !== undefined) {
        completion.resultJson = result_json;
      }
      if (input_json !== undefined) {
        completion.inputJson = input_json;
      }
      if (succeeded_count !== undefined) {
        completion.succeededCount = succeeded_count;
      }
      if (failed_count !== undefined) {
        completion.failedCount = failed_count;
      }
      const row = await flowRunRepo.updateCompletion(flowId, completion);
      if (!row) {
        apiLog("PUT", `/runs/${flowId}/completion`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/completion`, { flowId, status: 200, newStatus: status });
      res.json({ success: true, data: row });
      if (status === "failed") scheduleTerminalObserver(observers.onTerminal, { flowId, status });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/completion`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/fail-if-running — CAS claim for the engine timeout watchdog.
   *  Transitions the row to failed only when it is not already terminal, so
   *  exactly one engine process sharing this DB wins the reap. Always 200
   *  when the SQL ran; `claimed` tells the caller whether it won. */
  router.put("/:flowId/fail-if-running", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLogBody("PUT", `/runs/${flowId}/fail-if-running`, req.body, { flowId });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/fail-if-running`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { reason, current_phase, total_duration_ms, completed_at } = req.body as {
        reason?: string;
        current_phase?: string;
        total_duration_ms?: number | null;
        completed_at?: number;
      };
      if (!reason) {
        res.status(400).json({ success: false, error: "Bad Request", message: "reason is required" });
        return;
      }
      const claimed = await flowRunRepo.markFailedIfRunning(flowId, {
        reason,
        currentPhase: current_phase ?? "timeout",
        totalDurationMs: total_duration_ms ?? null,
        completedAt: completed_at ?? Math.floor(Date.now() / 1000),
      });
      apiLog("PUT", `/runs/${flowId}/fail-if-running`, { flowId, status: 200, claimed });
      res.json({ success: true, data: { claimed } });
      if (claimed) scheduleTerminalObserver(observers.onTerminal, { flowId, status: "failed" });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/fail-if-running`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/increment-node — increment succeeded or failed count */
  router.put("/:flowId/increment-node", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const { field } = req.body as { field?: string };
    apiLog("PUT", `/runs/${flowId}/increment-node`, { flowId, field });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/increment-node`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (field !== "succeeded_count" && field !== "failed_count") {
        apiLog("PUT", `/runs/${flowId}/increment-node`, { flowId, status: 400, error: "Invalid field" });
        res.status(400).json({ success: false, error: "Bad Request", message: "field must be 'succeeded_count' or 'failed_count'" });
        return;
      }

      const row = await flowRunRepo.incrementNodeCount(flowId, field);
      if (!row) {
        apiLog("PUT", `/runs/${flowId}/increment-node`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/increment-node`, { flowId, status: 200, field });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/increment-node`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/status — update status */
  router.put("/:flowId/status", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const { status } = req.body as { status?: string };
    apiLog("PUT", `/runs/${flowId}/status`, { flowId, newStatus: status });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/status`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (!status) {
        apiLog("PUT", `/runs/${flowId}/status`, { flowId, status: 400, error: "Missing status" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: status" });
        return;
      }

      const row = await flowRunRepo.updateStatus(flowId, status);
      if (!row) {
        apiLog("PUT", `/runs/${flowId}/status`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/status`, { flowId, status: 200, newStatus: status });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/status`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/phase — update current phase */
  router.put("/:flowId/phase", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const { phase } = req.body as { phase?: string };
    apiLog("PUT", `/runs/${flowId}/phase`, { flowId, phase });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/phase`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (!phase) {
        apiLog("PUT", `/runs/${flowId}/phase`, { flowId, status: 400, error: "Missing phase" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: phase" });
        return;
      }

      const row = await flowRunRepo.updateCurrentPhase(flowId, phase);
      if (!row) {
        apiLog("PUT", `/runs/${flowId}/phase`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/phase`, { flowId, status: 200, phase });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/phase`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/started-at — reset started_at before manual retry marks an old run running again. */
  router.put("/:flowId/started-at", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const { started_at } = req.body as { started_at?: number };
    apiLog("PUT", `/runs/${flowId}/started-at`, { flowId, started_at });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/started-at`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const startedAt = Number(started_at);
      if (!Number.isFinite(startedAt) || startedAt <= 0) {
        apiLog("PUT", `/runs/${flowId}/started-at`, { flowId, status: 400, error: "Invalid started_at" });
        res.status(400).json({ success: false, error: "Bad Request", message: "started_at must be a positive epoch-seconds number" });
        return;
      }

      const ok = await flowRunRepo.resetStartedAt(flowId, Math.floor(startedAt));
      if (!ok) {
        apiLog("PUT", `/runs/${flowId}/started-at`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/started-at`, { flowId, status: 200, startedAt: Math.floor(startedAt) });
      res.json({ success: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/started-at`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/node-count — update node count */
  router.put("/:flowId/node-count", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    const { node_count } = req.body as { node_count?: number };
    apiLog("PUT", `/runs/${flowId}/node-count`, { flowId, node_count });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/node-count`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      if (node_count === undefined || node_count === null) {
        apiLog("PUT", `/runs/${flowId}/node-count`, { flowId, status: 400, error: "Missing node_count" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: node_count" });
        return;
      }

      const row = await flowRunRepo.updateNodeCount(flowId, node_count);
      if (!row) {
        apiLog("PUT", `/runs/${flowId}/node-count`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/node-count`, { flowId, status: 200, node_count });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/node-count`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/result-json — overwrite result_json with last successful node output */
  router.put("/:flowId/result-json", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLogBody("PUT", `/runs/${flowId}/result-json`, req.body, { flowId });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { result_json } = req.body as { result_json?: string };
      if (!result_json || typeof result_json !== "string") {
        apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 400, error: "Missing or invalid result_json" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: result_json (JSON string)" });
        return;
      }

      // Parse back to extract nodeId + result for the repo method
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(result_json);
      } catch {
        apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 400, error: "Invalid result_json JSON" });
        res.status(400).json({ success: false, error: "Bad Request", message: "result_json must be valid JSON" });
        return;
      }

      const { nodeId, ...result } = parsed;
      if (!nodeId || typeof nodeId !== "string") {
        apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 400, error: "Missing nodeId in result_json" });
        res.status(400).json({ success: false, error: "Bad Request", message: "result_json must contain a nodeId field" });
        return;
      }

      const ok = await flowRunRepo.updateResultJson(flowId, nodeId, result);
      if (!ok) {
        apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 200, nodeId });
      res.json({ success: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/result-json`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/retry-failed — reset failed nodes to pending and update run status */
  router.put("/:flowId/retry-failed", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLog("PUT", `/runs/${flowId}/retry-failed`, { flowId });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/retry-failed`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        apiLog("PUT", `/runs/${flowId}/retry-failed`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }

      // Reset failed node executions
      let resetCount = 0;
      if (nodeExecRepo) {
        resetCount = await nodeExecRepo.resetFailedByFlowId(flowId);
      }

      // Decrement failed_count by the number of reset nodes (not blindly clear to 0)
      // and set status back to running
      const ok = await flowRunRepo.resetFailedForRetry(flowId, resetCount);
      if (!ok) {
        apiLog("PUT", `/runs/${flowId}/retry-failed`, { flowId, status: 500, error: "Failed to reset run" });
        res.status(500).json({ success: false, error: "Internal Server Error", message: "Failed to reset run for retry" });
        return;
      }

      apiLog("PUT", `/runs/${flowId}/retry-failed`, { flowId, status: 200, resetNodes: resetCount });
      res.json({ success: true, data: { flowId, resetNodes: resetCount } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/retry-failed`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:flowId/state — mirror the engine's TaskFlow stateJson (boundary writes) */
  router.put("/:flowId/state", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLogBody("PUT", `/runs/${flowId}/state`, req.body, { flowId });
    if (!flowRunRepo) {
      apiLog("PUT", `/runs/${flowId}/state`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { state_json } = req.body as { state_json?: string };
      if (!state_json || typeof state_json !== "string") {
        apiLog("PUT", `/runs/${flowId}/state`, { flowId, status: 400, error: "Missing or invalid state_json" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required field: state_json (JSON string)" });
        return;
      }
      try {
        JSON.parse(state_json);
      } catch {
        apiLog("PUT", `/runs/${flowId}/state`, { flowId, status: 400, error: "Invalid state_json JSON" });
        res.status(400).json({ success: false, error: "Bad Request", message: "state_json must be valid JSON" });
        return;
      }

      const ok = await flowRunRepo.updateStateJson(flowId, state_json);
      if (!ok) {
        apiLog("PUT", `/runs/${flowId}/state`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("PUT", `/runs/${flowId}/state`, { flowId, status: 200, bytes: state_json.length });
      res.json({ success: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("PUT", `/runs/${flowId}/state`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:flowId/state — read back the mirrored TaskFlow stateJson (null when not mirrored yet) */
  router.get("/:flowId/state", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLog("READ", `/runs/${flowId}/state`, { flowId });
    if (!flowRunRepo) {
      apiLog("READ", `/runs/${flowId}/state`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        apiLog("READ", `/runs/${flowId}/state`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      const stateJson = await flowRunRepo.getStateJson(flowId);
      apiLog("READ", `/runs/${flowId}/state`, { flowId, status: 200, hasState: stateJson !== null });
      res.json({ success: true, data: { state_json: stateJson } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", `/runs/${flowId}/state`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:flowId — find by flow id */
  router.get("/:flowId", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    apiLog("READ", `/runs/${flowId}`, { flowId });
    if (!flowRunRepo) {
      apiLog("READ", `/runs/${flowId}`, { flowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const row = await flowRunRepo.findFullByFlowId(String(flowId));
      if (!row) {
        apiLog("READ", `/runs/${flowId}`, { flowId, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Flow run "${flowId}" not found` });
        return;
      }
      apiLog("READ", `/runs/${flowId}`, { flowId, status: 200, workflowId: row.workflow_id, runStatus: row.status });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", `/runs/${flowId}`, { flowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — list runs */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    const status = req.query.status as string | undefined;
    const workflowId = req.query.workflowId as string | undefined;
    const limit = Math.min(parseInt(req.query.limit as string, 10) || 50, 200);
    const offset = parseInt(req.query.offset as string, 10) || 0;
    apiLog("READ", "/runs", { status, workflowId, limit, offset });

    // When flow control is disabled, short-circuit flow-control-related queries:
    // - status=blocked: "blocked" is a pure flow-control status (set when flow is
    //   queued/blocked by the dispatcher), so it cannot exist when flow control is off.
    // - status=waiting with high limit (>5): flow-control dispatcher queries
    //   waiting flows with limit=50 (findOrphanedWaitingFlows), while L1 intent
    //   fallback uses limit=1 for user approval/rejection — that must keep working.
    if (flowControlConfig?.enabled === false) {
      if (status === "blocked") {
        apiLog("READ", "/runs", { status: 200, shortcut: "flow-control-disabled", filterStatus: "blocked", count: 0 });
        res.json({ success: true, data: [], total: 0, limit, offset });
        return;
      }
      if (status === "waiting" && limit > 5) {
        apiLog("READ", "/runs", { status: 200, shortcut: "flow-control-disabled", filterStatus: "waiting", limit });
        res.json({ success: true, data: [], total: 0, limit, offset });
        return;
      }
    }

    if (!flowRunRepo) {
      apiLog("READ", "/runs", { status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rows = await flowRunRepo.findRuns({ status, workflowId, limit, offset });
      apiLog("READ", "/runs", { status: 200, count: rows.length, filterStatus: status, filterWorkflowId: workflowId });
      res.json({ success: true, data: rows, total: rows.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/runs", { status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}
