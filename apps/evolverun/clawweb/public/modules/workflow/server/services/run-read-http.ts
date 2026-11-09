/** Read-only delivery handlers extracted from runs.ts. Domain policy lives in RunSubworkflowsReader. */
import { Router, type Request, type Response } from "express";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { NodeExecutionRepository } from "../repositories/node-execution-repository.js";
import type { FlowEventRepository } from "../repositories/event-repository.js";
import type { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import type { ExecutionStepLogRepository } from "../repositories/execution-step-log-repository.js";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import { fixDurationMs, computeAvailableInterventions, toSafeRunFields } from "./run-read-fields.js";
import type { RunSubworkflowsReader, RunReadInspection } from "../contracts/run-subworkflows.js";
import { installFanoutReadHandlers, projectRun, projectNodes, sendRunReadError } from "./run-subworkflows-http.js";

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


export function createRunReadRouter(
  flowRunRepo: FlowRunRepository | null,
  nodeExecRepo: NodeExecutionRepository | null,
  eventRepo: FlowEventRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null,
  workflowSpecRepo: WorkflowSpecRepository | null,
  executionStepLogRepo: ExecutionStepLogRepository | null,
  runSubviews: RunSubworkflowsReader | null,
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
      const query = typeof _req.query.query === "string" ? _req.query.query.trim() || undefined : undefined;
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

      if (runSubviews) {
        const page = await runSubviews.listRuns({ actorId: resolveWorkflowActorId(_req), isAdmin: _req.isAdmin === true }, {
          status, statuses, workflowId, inputQuery, query, from, to, limit, offset,
          botId: typeof _req.query.botId === 'string' ? _req.query.botId.trim() : undefined,
        });
        res.setHeader('Cache-Control', 'private, no-store');
        res.json({ ...page, runs: page.runs.map(fixDurationMs) });
        return;
      }

      // View permission: botOwnerId optional, botId optional
      // Admin users bypass view permission — they see all data
      const queryBotOwnerId = _req.query.botOwnerId as string | undefined;
      const headerUserId = _req.headers["x-user-id"] as string | undefined;
      const botOwnerId = _req.isAdmin ? undefined : (queryBotOwnerId?.trim() || headerUserId?.trim() || _req.cookies?.staff_id?.trim() || resolveWorkflowActorId(_req) || undefined);
      const botId = (_req.query.botId as string | undefined)?.trim() || undefined;

      const viewPerm = _req.isAdmin ? null : await resolveViewPerm(botPermRepo, botOwnerId, botId);
      const allowedWorkflowIds = viewPerm === null
        ? undefined
        : [...viewPerm.viewableIds].filter((id) => viewPerm.restrictedIds.has(id));

      // Resolve view scope for the requested workflow: which bot runs can this user see?
      // undefined = no filtering (admin or no permission repo), [] = deny all, ['*'] = all bots.
      let allowedBotIds: string[] | undefined = undefined;
      if (!_req.isAdmin && botPermRepo && workflowId && botOwnerId) {
        const scope = await botPermRepo.resolveViewScope(workflowId, botOwnerId);
        if (scope === "deny") {
          allowedBotIds = [];
        } else if (scope === "all") {
          allowedBotIds = undefined;
        } else {
          allowedBotIds = scope.botIds;
        }
      }

      // If frontend explicitly requests a botId, intersect it with the resolved scope.
      // This preserves the old UI filter behavior while enforcing the permission boundary.
      if (botId) {
        if (allowedBotIds === undefined || allowedBotIds.includes("*")) {
          allowedBotIds = [botId];
        } else if (!allowedBotIds.includes(botId)) {
          allowedBotIds = [];
        } else {
          allowedBotIds = [botId];
        }
      }

      const countOptions = {
        allowedWorkflowIds,
        allowedBotIds,
        status: statuses.length === 0 ? status : undefined,
        statuses: statuses.length > 0 ? statuses : undefined,
        workflowId,
        from,
        to,
        inputQuery,
        query,
      };
      const total = await flowRunRepo.countRuns(countOptions);

      // Get status breakdown for accurate success-rate calculation (avoids pagination skew).
      // When a status filter is active the breakdown is trivial (all runs share that status),
      // so we only query when unfiltered.
      const statusCounts = !status && statuses.length === 0 && !inputQuery && !query
        ? await flowRunRepo.countByStatus(countOptions)
        : undefined;

      // Apply the same permission predicate before counting and pagination.
      const runs = await flowRunRepo.findRuns({ ...countOptions, limit, offset });

      res.json({ runs: runs.map(fixDurationMs), total, limit, offset, statusCounts });
    } catch (error) {
      sendRunReadError(res, error);
    }
  }));

  /** GET /workflow-types — list workflows from workflow_specs + flow_runs, with run stats */
  router.get("/workflow-types", asyncHandler(async (_req: Request, res: Response) => {
    try {
      const authorized = runSubviews ? await runSubviews.workflowStats({ actorId: resolveWorkflowActorId(_req), isAdmin: _req.isAdmin === true }) : null;
      // View permission: botOwnerId optional, botId optional
      // Admin users bypass view permission — they see all data
      const queryBotOwnerId = _req.query.botOwnerId as string | undefined;
      const headerUserId = _req.headers["x-user-id"] as string | undefined;
      const botOwnerId = _req.isAdmin ? undefined : (queryBotOwnerId?.trim() || headerUserId?.trim() || _req.cookies?.staff_id?.trim() || resolveWorkflowActorId(_req) || undefined);
      const botId = (_req.query.botId as string | undefined)?.trim() || undefined;
      const status = (_req.query.status as string | undefined)?.trim() || undefined;

      const viewPerm = authorized?.visibleWorkflowIds ? { restrictedIds: new Set(authorized.visibleWorkflowIds), viewableIds: new Set(authorized.visibleWorkflowIds) }
        : _req.isAdmin ? null : await resolveViewPerm(botPermRepo, botOwnerId, botId);

      // 1. Fetch spec summaries (no spec_json) + run stats in parallel
      const [specRows, runStats] = await Promise.all([
        workflowSpecRepo ? workflowSpecRepo.listSummaries() : Promise.resolve([]),
        authorized?.visibleWorkflowIds ? Promise.resolve(authorized.stats) : flowRunRepo ? flowRunRepo.findRunStatsByWorkflow() : Promise.resolve([]),
      ]);

      // 2. Build spec entries from lightweight summaries (title from DB column, no JSON parse)
      type SpecEntry = { workflowId: string; title: string; updatedAt: number | null; ownerId: string | null };
      const specEntries: SpecEntry[] = specRows.map((r) => ({
        workflowId: r.workflow_id,
        title: r.title ?? r.workflow_id,
        updatedAt: typeof r.gmt_modified === "string" ? Math.floor(new Date(r.gmt_modified).getTime() / 1000) : (r.gmt_modified ?? null),
        ownerId: r.resolved_owner_id ?? r.owner_id ?? null,
      }));

      // 3. Build run stats map
      type RunStats = { workflow_id: string; workflow_title: string | null; run_count: number; last_status: string | null; last_run_at: number | null };
      const runStatsMap = new Map<string, RunStats>();
      for (const s of runStats) {
        runStatsMap.set(s.workflow_id, s);
      }

      // 4. Merge workflow_specs entries + run-only workflows (flows without a spec still show up)
      type WorkflowEntry = { workflow_id: string; workflow_title: string | null; run_count: number; last_status: string | null; last_run_at: number | null; updated_at: number | null; owner_id: string | null };
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
          owner_id: spec.ownerId,
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
          owner_id: null,
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
      sendRunReadError(res, error);
    }
  }));

  installFanoutReadHandlers(router, runSubviews);

  /** GET /:flowId — get single run with node executions + intervention metadata */
  router.get("/:flowId", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo || !nodeExecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const inspection = res.locals.runRead as RunReadInspection | undefined;
      const run = inspection?.run ?? await flowRunRepo.findFullByFlowId(String(req.params.flowId));
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${req.params.flowId} not found` });
        return;
      }
      const nodes = await nodeExecRepo.findLatestByFlowId(run.flow_id);
      const availableInterventions = res.locals.runRead?.managed || res.locals.runRead?.sensitiveChild ? [] : computeAvailableInterventions(run);
      // Never expose credentials_json; add safe derived fields
      const safeRun = projectRun(toSafeRunFields(fixDurationMs(run) as Record<string, unknown>), inspection);
      res.json({ run: safeRun, nodes: projectNodes(nodes, inspection), availableInterventions });
    } catch (error) {
      sendRunReadError(res, error);
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
      const nodes = projectNodes(await nodeExecRepo.findByFlowId(flowId, { limit: 500 }), res.locals.runRead);

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
      sendRunReadError(res, error);
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
      res.json(res.locals.runRead?.managed || res.locals.runRead?.sensitiveChild ? [] : events);
    } catch (error) {
      sendRunReadError(res, error);
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
      const run = res.locals.runRead?.run ?? await flowRunRepo.findFullByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const inspection = res.locals.runRead as RunReadInspection | undefined;
      if (inspection?.sensitiveChild) {
        // The public write endpoints lack a verified production principal. Do not
        // offer actions or turn redacted session fields into editable settings.
        res.json({ flowId, status: run.status, canIntervene: false, availableInterventions: [],
          interventionReady: false, interventionReadOnly: true, sessionConfigured: inspection.sessionConfigured === true,
          originBotId: null, originSessionKey: null, originSessionId: null, hasCredentials: null });
        return;
      }
      const availableInterventions = inspection?.managed ? [] : computeAvailableInterventions(run);
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
      sendRunReadError(res, error);
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
      sendRunReadError(res, error);
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
      sendRunReadError(res, error);
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
      sendRunReadError(res, error);
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
      sendRunReadError(res, error);
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
