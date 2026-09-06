/**
 * Runs API routes — reads from flow_runs, node_executions, flow_events.
 * Matches ClawFlow's /api/runs endpoints for zero frontend changes.
 */
import { Router, type Request, type Response } from "express";
import { FlowRunRepository } from "../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../repositories/node-execution-repository.js";
import { FlowEventRepository } from "../repositories/event-repository.js";
import { MetricsRepository } from "../repositories/metrics-repository.js";
import { AlertRepository } from "../repositories/alert-repository.js";
import { FlowControlRepository } from "../repositories/flow-control-repository.js";
import type { BotWorkflowPermissionRepository } from "../repositories/bot-workflow-permission-repository.js";
import { sendIntervention } from "@avernet/clawevolve/server/services/baas-intervention";
import { buildInterventionMessage, type InterventionAction } from "../services/intervention-message-builder.js";
import type { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import { ExecutionStepLogRepository } from "../repositories/execution-step-log-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

/**
 * Fix total_duration_ms values that were inflated 1000x by a bug in
 * ClawMind's computeDurationMs() (which multiplied an already-ms difference by 1000).
 * The bug was: (now() - start) * 1000 where both now() and start are in milliseconds.
 * Detection heuristic: if total_duration_ms > (completed_at - started_at) * 1000 * 2
 * (i.e. more than 2x the wall-clock span in microseconds), divide by 1000.
 */
function fixDurationMs<T extends { started_at: number; completed_at: number | null; total_duration_ms: number | null }>(row: T): T {
  if (row.total_duration_ms == null || row.total_duration_ms === 0) return row
  if (row.completed_at == null || row.started_at == null) return row
  // started_at and completed_at are Unix seconds; wall-clock span in ms:
  const wallClockMs = (row.completed_at - row.started_at) * 1000
  if (wallClockMs <= 0) return row
  // If duration is > 2x the wall-clock span, it was inflated by the bug
  if (row.total_duration_ms > wallClockMs * 2) {
    return { ...row, total_duration_ms: Math.round(row.total_duration_ms / 1000) }
  }
  return row
}

/**
 * Resolve view permission filter for runs/workflow-types endpoints.
 * Returns null if unrestricted (show all), or { restrictedIds, viewableIds } for filtering.
 * Whitelist model: no permission record = not visible.
 */
async function resolveViewPerm(
  botPermRepo: BotWorkflowPermissionRepository | null,
  botOwnerId: string | undefined,
  botId: string | undefined,
): Promise<{ restrictedIds: Set<string>; viewableIds: Set<string> } | null> {
  if (!botPermRepo || !botOwnerId) return null; // no permission data or no owner = unrestricted
  return botPermRepo.getViewByIdsForOwner(botOwnerId, botId);
}

function applyViewPermFilter<T extends { workflow_id: string }>(items: T[], viewPerm: { restrictedIds: Set<string>; viewableIds: Set<string> } | null): T[] {
  if (viewPerm === null) return items;
  return items.filter((item) => {
    // No permission record → not visible (whitelist)
    if (!viewPerm.restrictedIds.has(item.workflow_id)) return false;
    // Workflow under access control → must be in viewableIds
    return viewPerm.viewableIds.has(item.workflow_id);
  });
}

/** Flow statuses that allow human intervention */
const INTERVENABLE_STATUSES = new Set(["failed", "blocked", "waiting"]);

/** Valid intervention actions */
const VALID_ACTIONS = new Set<InterventionAction>(["retry", "skip", "revise", "confirm"]);

/**
 * Compute which intervention actions are available for a flow run
 * based on its status and the credentials/session info it carries.
 */
function computeAvailableInterventions(run: {
  status: string;
  origin_bot_id: string | null;
  origin_session_key: string | null;
  credentials_json: string | null;
}): InterventionAction[] {
  if (!INTERVENABLE_STATUSES.has(run.status)) return [];
  // Must have BaaS routing info to send intervention messages
  if (!run.origin_bot_id || !run.origin_session_key) return [];

  const actions: InterventionAction[] = [];
  if (run.status === "failed" || run.status === "blocked") {
    actions.push("retry", "skip", "revise");
  }
  if (run.status === "waiting") {
    actions.push("confirm", "skip");
  }
  return actions;
}

/**
 * Strip credentials_json from a flow run row for safe API responses.
 * Adds derived safe fields instead of exposing raw credentials.
 */
function toSafeRunFields(run: Record<string, unknown>): Record<string, unknown> {
  const { credentials_json, ...safe } = run;
  return {
    ...safe,
    hasCredentials: credentials_json != null && credentials_json !== "",
  };
}

export function createRunsRouter(
  flowRunRepo: FlowRunRepository | null,
  nodeExecRepo: NodeExecutionRepository | null,
  eventRepo: FlowEventRepository | null,
  metricsRepo: MetricsRepository | null,
  alertRepo: AlertRepository | null,
  flowControlRepo: FlowControlRepository | null = null,
  botPermRepo: BotWorkflowPermissionRepository | null = null,
  workflowSpecRepo: WorkflowSpecRepository | null = null,
  executionStepLogRepo: ExecutionStepLogRepository | null = null,
): Router {
  const router = Router();

  /** GET / — list flow runs, filtered by view permission if botOwnerId provided */
  router.get("/", asyncHandler(async (_req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const status = _req.query.status as string | undefined;
      const statuses = String(_req.query.statuses ?? "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean)
        .slice(0, 10);
      const workflowId = _req.query.workflowId as string | undefined;
      const inputQuery = (_req.query.inputQuery as string | undefined)?.trim() || undefined;
      const limit = Math.min(parseInt(_req.query.limit as string, 10) || 30, 2000);
      const offset = parseInt(_req.query.offset as string, 10) || 0;
      // from/to accept ISO strings (e.g. "2026-07-22T08:41:14.023Z", what the frontend sends)
      // or epoch values in seconds/ms. Repository compares against started_at (Unix seconds),
      // so normalize to seconds here.
      const parseTimeParam = (raw: string | undefined): number | undefined => {
        if (!raw) return undefined;
        if (/^\d+$/.test(raw)) {
          const n = parseInt(raw, 10);
          return n > 1e12 ? Math.floor(n / 1000) : n; // ms → seconds
        }
        const t = Date.parse(raw);
        return Number.isNaN(t) ? undefined : Math.floor(t / 1000);
      };
      const from = parseTimeParam(_req.query.from as string | undefined);
      const to = parseTimeParam(_req.query.to as string | undefined);

      // View permission: botOwnerId optional, botId optional
      // Admin users bypass view permission — they see all data
      const queryBotOwnerId = _req.query.botOwnerId as string | undefined;
      const headerUserId = _req.headers["x-user-id"] as string | undefined;
      const botOwnerId = _req.isAdmin ? undefined : (queryBotOwnerId?.trim() || headerUserId?.trim() || _req.cookies?.staff_id?.trim() || undefined);
      const botId = (_req.query.botId as string | undefined)?.trim() || undefined;

      const viewPerm = _req.isAdmin ? null : await resolveViewPerm(botPermRepo, botOwnerId, botId);

      // origin_bot_id filtering: botOwnerId/botId also filter runs by their origin bot
      const originFilter = botOwnerId ? { originBotOwnerId: botOwnerId, originBotId: botId } : {};

      const countOptions = {
        status: statuses.length === 0 ? status : undefined,
        statuses: statuses.length > 0 ? statuses : undefined,
        workflowId,
        from,
        to,
        inputQuery,
        ...originFilter,
      };
      const total = await flowRunRepo.countRuns(countOptions);

      // Get status breakdown for accurate success-rate calculation (avoids pagination skew).
      // When a status filter is active the breakdown is trivial (all runs share that status),
      // so we only query when unfiltered.
      const statusCounts = !status && statuses.length === 0 && !inputQuery
        ? await flowRunRepo.countByStatus({ workflowId, from, to, ...originFilter })
        : undefined;

      // If permission filter is active, fetch more rows to compensate for filtered-out ones
      const fetchLimit = viewPerm !== null ? Math.min(limit * 5, 200) : limit;
      let runs = await flowRunRepo.findRuns({ ...countOptions, limit: fetchLimit, offset });

      runs = applyViewPermFilter(runs, viewPerm).slice(0, limit);

      res.json({ runs: runs.map(fixDurationMs), total, limit, offset, statusCounts });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /workflow-types — list workflows from workflow_specs + flow_runs, with run stats */
  router.get("/workflow-types", asyncHandler(async (_req: Request, res: Response) => {
    try {
      // View permission: botOwnerId optional, botId optional
      // Admin users bypass view permission — they see all data
      const queryBotOwnerId = _req.query.botOwnerId as string | undefined;
      const headerUserId = _req.headers["x-user-id"] as string | undefined;
      const botOwnerId = _req.isAdmin ? undefined : (queryBotOwnerId?.trim() || headerUserId?.trim() || _req.cookies?.staff_id?.trim() || undefined);
      const botId = (_req.query.botId as string | undefined)?.trim() || undefined;
      const status = (_req.query.status as string | undefined)?.trim() || undefined;

      const viewPerm = _req.isAdmin ? null : await resolveViewPerm(botPermRepo, botOwnerId, botId);

      // 1. Fetch spec summaries (no spec_json) + run stats in parallel
      const [specRows, runStats] = await Promise.all([
        workflowSpecRepo ? workflowSpecRepo.listSummaries() : Promise.resolve([]),
        flowRunRepo ? flowRunRepo.findRunStatsByWorkflow() : Promise.resolve([]),
      ]);

      // 2. Build spec entries from lightweight summaries (title from DB column, no JSON parse)
      type SpecEntry = { workflowId: string; title: string; updatedAt: number | null };
      const specEntries: SpecEntry[] = specRows.map((r) => ({
        workflowId: r.workflow_id,
        title: r.title ?? r.workflow_id,
        updatedAt: typeof r.gmt_modified === "string" ? Math.floor(new Date(r.gmt_modified).getTime() / 1000) : (r.gmt_modified ?? null),
      }));

      // 3. Build run stats map
      type RunStats = { workflow_id: string; workflow_title: string | null; run_count: number; last_status: string | null; last_run_at: number | null };
      const runStatsMap = new Map<string, RunStats>();
      for (const s of runStats) {
        runStatsMap.set(s.workflow_id, s);
      }

      // 4. Merge workflow_specs entries + run-only workflows (flows without a spec still show up)
      type WorkflowEntry = { workflow_id: string; workflow_title: string | null; run_count: number; last_status: string | null; last_run_at: number | null; updated_at: number | null };
      const merged: WorkflowEntry[] = [];
      const seenIds = new Set<string>();

      // 4a. Entries from workflow_specs (have a saved spec, may or may not have runs)
      for (const spec of specEntries) {
        seenIds.add(spec.workflowId);
        const stats = runStatsMap.get(spec.workflowId);
        merged.push({
          workflow_id: spec.workflowId,
          workflow_title: spec.title,
          run_count: stats?.run_count ?? 0,
          last_status: stats?.last_status ?? null,
          last_run_at: stats?.last_run_at ?? null,
          updated_at: spec.updatedAt,
        });
      }

      // 4b. Run-only workflows (have runs but no saved spec)
      for (const [wfId, stats] of runStatsMap) {
        if (seenIds.has(wfId)) continue;
        merged.push({
          workflow_id: wfId,
          workflow_title: stats.workflow_title ?? wfId,
          run_count: stats.run_count,
          last_status: stats.last_status ?? null,
          last_run_at: stats.last_run_at ?? null,
          updated_at: null,
        });
      }

      // 5. Apply status filter: match by last_status of the workflow
      let filtered = merged;
      if (status) {
        filtered = merged.filter((w) => w.last_status === status);
      }

      // 6. Apply view permission filter
      filtered = applyViewPermFilter(filtered, viewPerm);

      // 7. Sort: by last_run_at DESC (most recently run first); never-run workflows go to bottom, then by updated_at DESC
      filtered.sort((a, b) => {
        const aHasRun = a.last_run_at != null && a.last_run_at > 0;
        const bHasRun = b.last_run_at != null && b.last_run_at > 0;
        // Both have runs: compare by last_run_at DESC
        if (aHasRun && bHasRun) return b.last_run_at! - a.last_run_at!;
        // Only one has run: that one comes first
        if (aHasRun && !bHasRun) return -1;
        if (!aHasRun && bHasRun) return 1;
        // Neither has run: compare by updated_at DESC
        const aUpdated = a.updated_at ?? 0;
        const bUpdated = b.updated_at ?? 0;
        return bUpdated - aUpdated;
      });

      // 8. Paginate
      const limit = Math.min(parseInt(_req.query.limit as string, 10) || 100, 500);
      const offset = parseInt(_req.query.offset as string, 10) || 0;
      const total = filtered.length;
      const page = filtered.slice(offset, offset + limit);

      res.json({ workflows: page, total, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:flowId — get single run with node executions + intervention metadata */
  router.get("/:flowId", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo || !nodeExecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const run = await flowRunRepo.findFullByFlowId(String(req.params.flowId));
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${req.params.flowId} not found` });
        return;
      }
      const nodes = await nodeExecRepo.findLatestByFlowId(run.flow_id);
      const availableInterventions = computeAvailableInterventions(run);
      // Never expose credentials_json; add safe derived fields
      const safeRun = toSafeRunFields(fixDurationMs(run) as Record<string, unknown>);
      res.json({ run: safeRun, nodes, availableInterventions });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:flowId/nodes — list node executions with optional truncation */
  router.get("/:flowId/nodes", asyncHandler(async (req: Request, res: Response) => {
    if (!nodeExecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const full = req.query.full === "true";
      const nodes = await nodeExecRepo.findByFlowId(flowId, { limit: 500 });

      if (!full) {
        const TRUNCATE_LIMIT = 10 * 1024;
        const truncated = nodes.map((node) => ({
          ...node,
          input_json: truncateField(node.input_json, TRUNCATE_LIMIT),
          output_json: truncateField(node.output_json, TRUNCATE_LIMIT),
        }));
        res.json(truncated);
      } else {
        res.json(nodes);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:flowId/events — list flow events */
  router.get("/:flowId/events", asyncHandler(async (req: Request, res: Response) => {
    if (!eventRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const limit = Math.min(parseInt(req.query.limit as string, 10) || 200, 1000);
      const events = await eventRepo.findByFlowId(flowId, { limit });
      res.json(events);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:flowId/interventions — query available intervention actions for a flow */
  router.get("/:flowId/interventions", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const run = await flowRunRepo.findFullByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const availableInterventions = computeAvailableInterventions(run);
      const canIntervene = availableInterventions.length > 0;

      res.json({
        flowId,
        status: run.status,
        canIntervene,
        availableInterventions,
        interventionReady: canIntervene && !!run.origin_bot_id && !!run.origin_session_key,
        originBotId: run.origin_bot_id,
        originSessionKey: run.origin_session_key,
        originSessionId: run.origin_session_id,
        hasCredentials: run.credentials_json != null && run.credentials_json !== "",
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** PATCH /:flowId/session — update BaaS session info for a flow run */
  router.patch("/:flowId/session", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { originBotId, originSessionKey, originSessionId } = req.body as {
        originBotId?: string | null;
        originSessionKey?: string | null;
        originSessionId?: string | null;
      };

      // At least one field must be provided
      if (originBotId === undefined && originSessionKey === undefined && originSessionId === undefined) {
        res.status(400).json({
          error: "Bad Request",
          message: "至少需要提供 originBotId、originSessionKey 或 originSessionId 之一",
        });
        return;
      }

      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const updated = await flowRunRepo.updateSessionInfo(flowId, {
        originBotId,
        originSessionKey,
        originSessionId,
      });

      if (!updated) {
        res.status(500).json({ error: "Internal Server Error", message: "Failed to update session info" });
        return;
      }

      // Return updated session info (never expose credentials_json)
      const refreshed = await flowRunRepo.findByFlowId(flowId);
      res.json({
        ok: true,
        flowId,
        originBotId: refreshed?.origin_bot_id ?? null,
        originSessionKey: refreshed?.origin_session_key ?? null,
        originSessionId: refreshed?.origin_session_id ?? null,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/intervene — send human intervention message via BaaS */
  router.post("/:flowId/intervene", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { action, nodeId, nodeTitle, reason } = req.body as {
        action?: string;
        nodeId?: string;
        nodeTitle?: string;
        reason?: string;
      };

      // Validate action
      if (!action || !VALID_ACTIONS.has(action as InterventionAction)) {
        res.status(400).json({
          error: "Bad Request",
          message: `Invalid action "${action}". Must be one of: ${[...VALID_ACTIONS].join(", ")}`,
        });
        return;
      }

      const typedAction = action as InterventionAction;

      // Load flow run
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Check if flow is in an intervenable status
      if (!INTERVENABLE_STATUSES.has(run.status)) {
        res.status(409).json({
          error: "Conflict",
          message: `流程状态为 "${run.status}"，无法干预。仅支持状态: ${[...INTERVENABLE_STATUSES].join(", ")}`,
        });
        return;
      }

      // Check if BaaS routing info is available
      if (!run.origin_bot_id || !run.origin_session_key) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少 BaaS 会话信息 (origin_bot_id / origin_session_key)，无法发送干预消息",
          hint: "流程可能不是从 BaaS 会话启动的，或版本过旧未记录会话信息",
        });
        return;
      }

      // Check if action is valid for this status
      const available = computeAvailableInterventions(run);
      if (!available.includes(typedAction)) {
        res.status(409).json({
          error: "Conflict",
          message: `操作 "${typedAction}" 在当前状态 "${run.status}" 下不可用。可用操作: ${available.join(", ") || "无"}`,
          availableInterventions: available,
        });
        return;
      }

      // Get operator identity from headers
      const operatorId = (req.headers["x-user-id"] as string | undefined)?.trim() || "unknown";
      const operatorName = (req.headers["x-user-name"] as string | undefined)?.trim() || operatorId;

      // Build intervention message
      const message = buildInterventionMessage({
        action: typedAction,
        flowId,
        workflowTitle: run.workflow_title ?? undefined,
        nodeId,
        nodeTitle,
        reason,
        operatorId,
        operatorName,
      });

      // Send via BaaS
      const result = await sendIntervention({
        botId: run.origin_bot_id,
        sessionKey: run.origin_session_key,
        sessionId: run.origin_session_id,
        message,
      });

      if (!result.ok) {
        const statusCode = result.tokenExpired ? 401 : 502;
        res.status(statusCode).json({
          error: result.tokenExpired ? "Unauthorized" : "Bad Gateway",
          message: result.error,
          tokenExpired: result.tokenExpired,
        });
        return;
      }

      // Persist BaaS session_id so subsequent interventions use the same session (multi-turn)
      if (result.sessionId && result.sessionId !== run.origin_session_id) {
        try {
          await flowRunRepo.updateSessionInfo(flowId, {
            originSessionId: result.sessionId,
          });
        } catch (updateErr) {
          console.warn(`[runs] failed to persist session_id for ${flowId}:`, updateErr);
        }
      }

      // Record intervention event for audit trail
      if (eventRepo) {
        try {
          await eventRepo.insert({
            id: `intervention-${flowId}-${Date.now()}`,
            time: Math.floor(Date.now() / 1000),
            type: "human_intervention",
            flowId,
            workflowId: run.workflow_id,
            nodeId: nodeId ?? null,
            data: {
              action: typedAction,
              operatorId,
              operatorName,
              reason: reason ?? null,
              baasMessageId: result.messageId,
              baasSessionId: result.sessionId,
            },
          });
        } catch (eventErr) {
          // Audit logging is best-effort, don't fail the intervention
          console.warn(`[runs] failed to record intervention event for ${flowId}:`, eventErr);
        }
      }

      res.json({
        ok: true,
        flowId,
        action: typedAction,
        messageId: result.messageId,
        sessionId: result.sessionId,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/rerun — re-trigger a workflow by resending the original command to origin_bot_id */
  router.post("/:flowId/rerun", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);

      // Load flow run (full row including input_json)
      const run = await flowRunRepo.findFullByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Validate origin_bot_id — must exist to send the command
      if (!run.origin_bot_id) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少 origin_bot_id，无法重跑。流程可能不是从 BaaS 会话启动的。",
        });
        return;
      }

      // Parse input_json to extract the command + message
      // input_json shape: { command: "/kf-direct", message: "什么是任务", ... }
      // The bot expects: "/kf-direct 什么是任务" (command + space + message)
      let command: string | null = null;
      if (run.input_json) {
        try {
          const parsed = JSON.parse(run.input_json) as Record<string, unknown>;
          const cmd = (parsed.command as string | undefined)?.trim() ?? null;
          const msg = (parsed.message as string | undefined)?.trim() ?? null;
          if (cmd && msg) {
            command = `${cmd} ${msg}`;
          } else if (cmd) {
            command = cmd;
          }
        } catch {
          // input_json is not valid JSON — fall through
        }
      }

      if (!command) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少可执行命令 (input_json.command)，无法重跑。",
        });
        return;
      }

      // Get operator identity
      const operatorId = (req.headers["x-user-id"] as string | undefined)?.trim() || "unknown";
      const operatorName = (req.headers["x-user-name"] as string | undefined)?.trim() || operatorId;

      // Build BaaS message — send the original command to the original bot
      const message = `🔄 [重跑] ${operatorName}(${operatorId}) 重新触发工作流\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📁 工作流: ${run.workflow_id}${run.workflow_title ? ` (${run.workflow_title})` : ""}\n📎 原始运行: ${flowId}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n执行命令:\n${command}`;

      // Send via BaaS — use a fresh session key since this is a new invocation
      const result = await sendIntervention({
        botId: run.origin_bot_id,
        sessionKey: `rerun:${run.workflow_id}:${Date.now()}`,
        sessionId: null,
        message,
      });

      if (!result.ok) {
        const statusCode = result.tokenExpired ? 401 : 502;
        res.status(statusCode).json({
          error: result.tokenExpired ? "Unauthorized" : "Bad Gateway",
          message: `重跑发送失败: ${result.error ?? "unknown error"}`,
          tokenExpired: result.tokenExpired,
        });
        return;
      }

      // Record rerun event for audit trail
      if (eventRepo) {
        try {
          await eventRepo.insert({
            id: `rerun-${flowId}-${Date.now()}`,
            time: Math.floor(Date.now() / 1000),
            type: "rerun_triggered",
            flowId,
            workflowId: run.workflow_id,
            nodeId: null,
            data: {
              action: "rerun",
              operatorId,
              operatorName,
              originalFlowId: flowId,
              command,
              originBotId: run.origin_bot_id,
              baasMessageId: result.messageId,
              baasSessionId: result.sessionId,
            },
          });
        } catch (eventErr) {
          console.warn(`[runs] failed to record rerun event for ${flowId}:`, eventErr);
        }
      }

      res.json({
        ok: true,
        flowId,
        newFlowId: result.messageId ?? null,
        sessionId: result.sessionId ?? null,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/chat — send a free-text chat message via BaaS (multi-turn conversation) */
  router.post("/:flowId/chat", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { message } = req.body as { message?: string };

      if (!message || !message.trim()) {
        res.status(400).json({ error: "Bad Request", message: "消息内容不能为空" });
        return;
      }

      // Load flow run
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Check BaaS routing info
      if (!run.origin_bot_id || !run.origin_session_key) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少 BaaS 会话信息 (origin_bot_id / origin_session_key)，无法发送消息",
        });
        return;
      }

      // Get operator identity
      const operatorId = (req.headers["x-user-id"] as string | undefined)?.trim() || "unknown";

      // Send via BaaS — no action validation, just pass the message through
      const result = await sendIntervention({
        botId: run.origin_bot_id,
        sessionKey: run.origin_session_key,
        sessionId: run.origin_session_id,
        message: message.trim(),
      });

      if (!result.ok) {
        const statusCode = result.tokenExpired ? 401 : 502;
        res.status(statusCode).json({
          error: result.tokenExpired ? "Unauthorized" : "Bad Gateway",
          message: result.error,
          tokenExpired: result.tokenExpired,
        });
        return;
      }

      // Persist BaaS session_id so subsequent chat messages use the same session (multi-turn)
      // First call: origin_session_id is null → BaaS creates a new session and returns session_id.
      // Subsequent calls: origin_session_id is set → BaaS routes to the same session.
      if (result.sessionId && result.sessionId !== run.origin_session_id) {
        try {
          await flowRunRepo.updateSessionInfo(flowId, {
            originSessionId: result.sessionId,
          });
        } catch (updateErr) {
          console.warn(`[runs] failed to persist session_id for ${flowId}:`, updateErr);
        }
      }

      // Record chat event for audit trail
      if (eventRepo) {
        try {
          await eventRepo.insert({
            id: `chat-${flowId}-${Date.now()}`,
            time: Math.floor(Date.now() / 1000),
            type: "human_intervention",
            flowId,
            workflowId: run.workflow_id,
            nodeId: null,
            data: {
              action: "chat",
              operatorId,
              message: message.trim().substring(0, 500),
              baasMessageId: result.messageId,
              baasSessionId: result.sessionId,
            },
          });
        } catch (eventErr) {
          console.warn(`[runs] failed to record chat event for ${flowId}:`, eventErr);
        }
      }

      res.json({
        ok: true,
        flowId,
        messageId: result.messageId,
        sessionId: result.sessionId,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/poll-message — poll BaaS message result by message_id */
  router.post("/:flowId/poll-message", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { messageId } = req.body as { messageId?: string };

      if (!messageId) {
        res.status(400).json({ error: "Bad Request", message: "messageId is required" });
        return;
      }

      // Validate messageId format (prevent injection)
      if (!/^[a-zA-Z0-9_-]{1,256}$/.test(messageId)) {
        res.status(400).json({ error: "Bad Request", message: "Invalid messageId format" });
        return;
      }

      // Load flow to get BaaS config
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Resolve BaaS config for auth headers
      const { resolveBaasConfig } = await import("@avernet/clawweb-shared/server/db");
      const config = resolveBaasConfig();

      if (!config.apiKey) {
        res.status(500).json({ error: "Internal Server Error", message: "BaaS apiKey 未配置" });
        return;
      }

      // Build URL — use baseUrl from config, fallback to known BaaS origins
      const baseUrl = config.baseUrl || "https://baas-api.alipay.com";
      const pollUrl = `${baseUrl}/openapi/v1/messages/${encodeURIComponent(messageId)}`;

      // Build headers — iamtoken only on macOS (local dev)
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.apiKey}`,
      };
      if (process.platform === "darwin" && config.iamtoken) {
        headers["Cookie"] = `iam_token=${config.iamtoken}`;
      }

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);

      try {
        const pollRes = await fetch(pollUrl, {
          method: "GET",
          headers,
          signal: controller.signal,
        });

        let body: Record<string, unknown>;
        try {
          body = (await pollRes.json()) as Record<string, unknown>;
        } catch {
          body = {};
        }

        const data = (body.data ?? null) as Record<string, unknown> | null;

        res.json({
          ok: pollRes.ok,
          status: pollRes.status,
          data: data
            ? {
                messageId: (data as Record<string, unknown>).message_id ?? messageId,
                sessionId: (data as Record<string, unknown>).session_id ?? null,
                messageStatus: (data as Record<string, unknown>).status ?? "UNKNOWN",
                result: (data as Record<string, unknown>).result ?? null,
              }
            : null,
          errorCode: body.buserviceErrorCode ?? body.code ?? null,
          errorMessage: body.buserviceErrorMsg ?? body.message ?? null,
        });
      } catch (err) {
        if (controller.signal.aborted) {
          res.status(504).json({ error: "BaaS poll request timed out" });
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        res.status(502).json({ error: `BaaS poll request failed: ${msg}` });
      } finally {
        clearTimeout(timeout);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:flowId — delete a flow run and all related data */
  router.delete("/:flowId", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo || !nodeExecRepo || !eventRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Release flow control slots and delete queue entries for this flow
      // This prevents slot leaks when deleting running/waiting/blocked flows
      if (flowControlRepo) {
        try {
          const slotsReleased = await flowControlRepo.releaseAllSlotsForFlowByFlowId(flowId);
          const queueDeleted = await flowControlRepo.deleteQueueEntriesForFlowByFlowId(flowId);
          if (slotsReleased > 0 || queueDeleted > 0) {
            console.log(`[runs] released flow-control for deleted flow ${flowId}: slots=${slotsReleased}, queue=${queueDeleted}`);
          }
        } catch (fcErr) {
          // Flow control cleanup is best-effort — don't block the delete
          console.warn(`[runs] failed to release flow-control for flow ${flowId}:`, fcErr);
        }
      }

      // Delete child records first (order matters for FK safety if any)
      const nodesDeleted = await nodeExecRepo.deleteByFlowId(flowId);
      const eventsDeleted = await eventRepo.deleteByFlowId(flowId);
      const metricsDeleted = metricsRepo ? await metricsRepo.deleteByFlowId(flowId) : 0;
      const alertsDeleted = alertRepo ? await alertRepo.deleteByFlowId(flowId) : 0;
      const runDeleted = await flowRunRepo.deleteByFlowId(flowId);

      res.json({
        deleted: runDeleted,
        details: { nodes: nodesDeleted, events: eventsDeleted, metrics: metricsDeleted, alerts: alertsDeleted },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  // ── Dynamic Workflow endpoints ──

  /** GET /:id/dynamic — get injected nodes and orchestration state for a flow run */
  router.get("/:id/dynamic", asyncHandler(async (req: Request, res: Response) => {
    if (!executionStepLogRepo || !nodeExecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.id);

      // Find injected nodes from step logs (step_type = 'inject')
      const injectSteps = await executionStepLogRepo.getStepsByFlow(flowId, {
        stepType: "inject",
        limit: 1000,
      });

      // Find materialized nodes from step logs (step_type = 'materialize')
      const materializeSteps = await executionStepLogRepo.getStepsByFlow(flowId, {
        stepType: "materialize",
        limit: 1000,
      });

      // Find LLM evaluation steps (step_type = 'llm_evaluate' or 'goal_check')
      const [llmEvaluateSteps, goalCheckSteps] = await Promise.all([
        executionStepLogRepo.getStepsByFlow(flowId, { stepType: "llm_evaluate", limit: 1000 }),
        executionStepLogRepo.getStepsByFlow(flowId, { stepType: "goal_check", limit: 1000 }),
      ]);

      // Find budget events
      const [budgetWarnings, budgetExhausted] = await Promise.all([
        executionStepLogRepo.getStepsByFlow(flowId, { stepType: "budget_warning", limit: 100 }),
        executionStepLogRepo.getStepsByFlow(flowId, { stepType: "budget_exhausted", limit: 100 }),
      ]);

      // Identify orchestrator nodes from node_executions (executor_type contains 'llm-orchestrator')
      const allNodes = await nodeExecRepo.findByFlowId(flowId, { limit: 500 });
      const orchestratorNodes = allNodes.filter(
        (n) => n.executor_type === "llm-orchestrator" || n.node_id.includes("__step"),
      );

      // Build injectedNodes list from step logs
      const injectedNodes = injectSteps.map((step) => ({
        nodeId: step.node_id,
        stepType: step.step_type,
        timestamp: step.timestamp,
        decisionPath: step.decision_path,
        metadata: step.metadata ? JSON.parse(step.metadata) : null,
      }));

      // Build orchestrationState from orchestrator nodes and their iterations
      const orchestrationState = orchestratorNodes.map((node) => ({
        nodeId: node.node_id,
        executorType: node.executor_type,
        status: node.status,
        iterations: injectSteps
          .filter((s) => s.node_id.startsWith(node.node_id.replace(/__step\d+__.+$/, "")))
          .map((s) => ({
            nodeId: s.node_id,
            timestamp: s.timestamp,
            decisionPath: s.decision_path,
          })),
      }));

      res.json({
        success: true,
        data: {
          injectedNodes,
          materializedNodes: materializeSteps.map((s) => ({
            nodeId: s.node_id,
            timestamp: s.timestamp,
            inputSummary: s.input_summary,
            outputSummary: s.output_summary,
          })),
          orchestrationState,
          llmEvaluations: [...llmEvaluateSteps, ...goalCheckSteps].map((s) => ({
            nodeId: s.node_id,
            stepType: s.step_type,
            timestamp: s.timestamp,
            evaluation: s.llm_evaluation,
            tokenUsage: s.token_usage,
          })),
          budgetEvents: {
            warnings: budgetWarnings.map((s) => ({ nodeId: s.node_id, timestamp: s.timestamp, metadata: s.metadata ? JSON.parse(s.metadata) : null })),
            exhausted: budgetExhausted.map((s) => ({ nodeId: s.node_id, timestamp: s.timestamp, metadata: s.metadata ? JSON.parse(s.metadata) : null })),
          },
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:id/topology — get static + dynamic node topology for a flow run */
  router.get("/:id/topology", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo || !nodeExecRepo || !workflowSpecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.id);

      // Get the flow run to find its workflow_id
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Get the workflow spec for static node definitions
      const specRow = await workflowSpecRepo.findByWorkflowId(run.workflow_id);
      let staticNodes: unknown[] = [];
      if (specRow?.spec_json) {
        try {
          const spec = JSON.parse(specRow.spec_json);
          staticNodes = (spec.nodes ?? []).map((n: Record<string, unknown>) => ({
            id: n.id,
            title: n.title ?? n.id,
            phase: n.phase ?? "main",
            executorType: (n.executor as Record<string, unknown>)?.type ?? null,
            dependsOn: n.dependsOn ?? [],
            isDynamic: false,
          }));
        } catch {
          staticNodes = [];
        }
      }

      // Get all executed nodes to find dynamic ones
      const executedNodes = await nodeExecRepo.findByFlowId(flowId, { limit: 500 });
      const staticNodeIds = new Set((staticNodes as Array<{ id: string }>).map((n) => n.id));

      // Dynamic nodes: executed but not in static spec
      const dynamicNodes = executedNodes
        .filter((n) => !staticNodeIds.has(n.node_id))
        .map((n) => ({
          id: n.node_id,
          title: n.node_title ?? n.node_id,
          executorType: n.executor_type,
          status: n.status,
          isDynamic: true,
          expansionType: n.node_id.includes("__iter")
            ? "loop-group"
            : n.node_id.includes("__item")
              ? "dynamic-template"
              : n.node_id.includes("__step")
                ? "orchestrator"
                : "unknown",
        }));

      // Get materialize/inject step logs for expansion details
      const [materializeSteps, injectSteps] = await Promise.all([
        executionStepLogRepo
          ? executionStepLogRepo.getStepsByFlow(flowId, { stepType: "materialize", limit: 500 })
          : Promise.resolve([]),
        executionStepLogRepo
          ? executionStepLogRepo.getStepsByFlow(flowId, { stepType: "inject", limit: 500 })
          : Promise.resolve([]),
      ]);

      const expansionEvents = [
        ...materializeSteps.map((s) => ({
          nodeId: s.node_id,
          eventType: "materialize" as const,
          timestamp: s.timestamp,
          inputSummary: s.input_summary,
          outputSummary: s.output_summary,
        })),
        ...injectSteps.map((s) => ({
          nodeId: s.node_id,
          eventType: "inject" as const,
          timestamp: s.timestamp,
          decisionPath: s.decision_path,
          inputSummary: s.input_summary,
          outputSummary: null as string | null,
        })),
      ];

      res.json({
        success: true,
        data: {
          workflowId: run.workflow_id,
          workflowTitle: run.workflow_title,
          flowStatus: run.status,
          staticNodes,
          dynamicNodes,
          expansionEvents,
          summary: {
            staticNodeCount: staticNodes.length,
            dynamicNodeCount: dynamicNodes.length,
            expansionEventCount: expansionEvents.length,
          },
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  // ── Execution Step Log endpoints ──

  /** GET /:id/steps — get execution step logs for a flow run */
  router.get("/:id/steps", asyncHandler(async (req: Request, res: Response) => {
    if (!executionStepLogRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = typeof req.params.id === "string" ? req.params.id : String(req.params.id ?? "");
      const nodeId = typeof req.query.nodeId === "string" ? req.query.nodeId : undefined;
      const stepType = typeof req.query.stepType === "string" ? req.query.stepType : undefined;
      const limit = typeof req.query.limit === "string" ? parseInt(req.query.limit, 10) : 100;
      const offset = typeof req.query.offset === "string" ? parseInt(req.query.offset, 10) : 0;

      const steps = await executionStepLogRepo.getStepsByFlow(flowId, {
        nodeId,
        stepType,
        limit,
        offset,
      });
      const count = await executionStepLogRepo.getStepCountByFlow(flowId, { nodeId, stepType });

      res.json({ success: true, data: steps, meta: { total: count, limit, offset } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:id/replay — get execution step logs in chronological order for timeline replay */
  router.get("/:id/replay", asyncHandler(async (req: Request, res: Response) => {
    if (!executionStepLogRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = typeof req.params.id === "string" ? req.params.id : String(req.params.id ?? "");

      const steps = await executionStepLogRepo.getStepsByFlow(flowId, {
        limit: 10000,
        offset: 0,
      });

      res.json({ success: true, data: steps });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}

function truncateField(value: string | null, maxBytes: number): string | null {
  if (!value) return null;
  const byteLength = Buffer.byteLength(value, "utf-8");
  if (byteLength <= maxBytes) return value;
  return value.substring(0, maxBytes) + `... [truncated, ${byteLength} bytes total]`;
}
