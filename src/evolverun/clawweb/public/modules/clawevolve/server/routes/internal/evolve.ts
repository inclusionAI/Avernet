/**
 * Internal evolve routes — called by ClawMind plugin / business bot.
 *
 * clawweb 只负责：
 *   - 读取 run 级日志（原料）
 *   - 接收并存储 bot 回写的 diagnosis / lesson / outcome / suggestion
 *
 * 所有 LLM 分析都发生在 ClawMind plugin 内。
 */
import { Router, type Request, type Response } from "express";
import type { IDatabase, Row } from "@avernet/clawweb-shared/server/db";
import type { EvolveRepository } from "../../repositories/evolve-repository.js";
import type { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import type { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import type { NodeStepTraceRepository } from "../../repositories/node-step-traces-repository.js";
import type { RunLogRepository } from "../../repositories/run-log-repository.js";
import type { ExecutionStepLogRepository } from "../../repositories/execution-step-log-repository.js";
import crypto from "node:crypto";
import { buildRunTimeline } from "../../lib/timeline-builder.js";
import { digestCanonicalJson, validateWorkflowEvolutionAnalysisResult } from "../../services/evolution/contracts.js";
import {
  WorkflowAnalysisStateConflictError,
  WorkflowEvidenceDigestConflictError,
  WorkflowEvolutionRepository,
  type WorkflowEvolutionAnalysisRow,
  type WorkflowRunEvidenceInput,
} from "../../repositories/workflow-evolution-repository.js";

function positiveInt(value: unknown, fallback: number, max: number): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(Math.floor(n), max);
}

function textOrNull(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function getActor(req: Request): string {
  return String(req.header("X-User-Id") ?? "").trim();
}

function assertRepo(repo: EvolveRepository | null): asserts repo is EvolveRepository {
  if (!repo) throw new Error("Evolve repository not configured");
}

class AnalysisBotMismatchError extends Error {}

const ANALYSIS_PROGRESS_PHASES = [
  "loading_input",
  "input_ready",
  "agent_analyzing",
  "validating",
  "persisting",
  "completed",
  "failed",
] as const;

type AnalysisProgressPhase = typeof ANALYSIS_PROGRESS_PHASES[number];

export interface InternalEvolveRepos {
  db: IDatabase;
  evolveRepo: EvolveRepository | null;
  flowRunRepo: FlowRunRepository | null;
  nodeExecRepo: NodeExecutionRepository | null;
  nodeStepTraceRepo: NodeStepTraceRepository | null;
  runLogRepo?: RunLogRepository | null;
  executionStepLogRepo?: ExecutionStepLogRepository | null;
}

export function createInternalEvolveRouter(repos: InternalEvolveRepos): Router {
  const router = Router();
  const { db, evolveRepo, flowRunRepo, nodeExecRepo, nodeStepTraceRepo, runLogRepo, executionStepLogRepo } = repos;
  const workflowEvolutionRepo = new WorkflowEvolutionRepository(db);

  const assertLinkedAnalysisBot = async (
    analysis: WorkflowEvolutionAnalysisRow,
    requestedBotId: string | null,
  ): Promise<void> => {
    if (!analysis.task_id || !analysis.step_id) return;
    assertRepo(evolveRepo);
    const [task, step] = await Promise.all([
      evolveRepo.findTask(analysis.task_id),
      evolveRepo.findStep(analysis.step_id),
    ]);
    if (!task || !step || step.task_id !== analysis.task_id || step.step_type !== "run_analysis") {
      throw new Error(`linked run_analysis task is invalid: ${analysis.task_id}/${analysis.step_id}`);
    }
    if (task.bot_id && requestedBotId !== task.bot_id) {
      throw new AnalysisBotMismatchError(`analysis ${analysis.analysis_id} is assigned to another bot`);
    }
  };

  const settleLinkedAnalysisTask = async (
    analysis: WorkflowEvolutionAnalysisRow,
    terminal: "completed" | "failed",
  ): Promise<void> => {
    if (!evolveRepo || !analysis.task_id || !analysis.step_id) return;
    const step = await evolveRepo.findStep(analysis.step_id);
    if (!step || step.task_id !== analysis.task_id || step.step_type !== "run_analysis") {
      throw new Error(`linked run_analysis step is invalid: ${analysis.task_id}/${analysis.step_id}`);
    }
    const targetStepStatus = terminal === "completed" ? "succeeded" : "failed";
    if (step.status !== targetStepStatus) {
      if (["succeeded", "failed", "canceled"].includes(step.status)) {
        throw new Error(`linked run_analysis step is already ${step.status}`);
      }
      await evolveRepo.updateStepStatus(analysis.step_id, terminal === "completed" ? {
        status: "succeeded",
        summary: `进化分析 ${analysis.analysis_id} 已完成`,
        output: {
          analysisId: analysis.analysis_id,
          flowId: analysis.flow_id,
          workflowId: analysis.workflow_id,
          diagnosisCount: analysis.diagnosis_count,
        },
      } : {
        status: "failed",
        summary: `进化分析 ${analysis.analysis_id} 失败`,
        errorCode: analysis.error_code ?? "ANALYSIS_FAILED",
        errorMessage: analysis.error_code ?? "进化分析失败",
        retryable: true,
      });
    }
    if (terminal === "completed") await evolveRepo.completeTask(analysis.task_id);
  };

  const requiredBodyText = (value: unknown, field: string, maxLength: number): string => {
    if (typeof value !== "string" || !value.trim() || value.length > maxLength || value.includes("\0")) {
      throw new Error(`${field} is invalid`);
    }
    return value.trim();
  };

  router.post("/run-evidence/batch", async (req: Request, res: Response) => {
    try {
      const rawEvents = (req.body as { events?: unknown } | undefined)?.events;
      if (!Array.isArray(rawEvents)) {
        res.status(400).json({ error: "Bad Request", message: "events must be an array" });
        return;
      }
      const events: WorkflowRunEvidenceInput[] = rawEvents.map((raw, index) => {
        if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`events[${index}] is invalid`);
        const event = raw as Record<string, unknown>;
        const eventSeq = Number(event.eventSeq);
        const occurredAtMs = Number(event.occurredAtMs);
        if (!Number.isSafeInteger(eventSeq) || eventSeq < 0) throw new Error(`events[${index}].eventSeq is invalid`);
        if (!Number.isSafeInteger(occurredAtMs) || occurredAtMs <= 0) throw new Error(`events[${index}].occurredAtMs is invalid`);
        return {
          eventId: requiredBodyText(event.eventId, `events[${index}].eventId`, 64),
          payloadDigest: requiredBodyText(event.payloadDigest, `events[${index}].payloadDigest`, 64),
          flowId: requiredBodyText(event.flowId, `events[${index}].flowId`, 190),
          workflowId: requiredBodyText(event.workflowId, `events[${index}].workflowId`, 190),
          nodeId: textOrNull(event.nodeId),
          eventType: requiredBodyText(event.eventType, `events[${index}].eventType`, 64),
          producer: requiredBodyText(event.producer, `events[${index}].producer`, 64),
          eventSeq,
          occurredAtMs,
          payload: event.payload,
        };
      });
      res.json({ receipts: await workflowEvolutionRepo.appendEvidenceBatch(events) });
    } catch (error) {
      if (error instanceof WorkflowEvidenceDigestConflictError) {
        res.status(409).json({ error: "evidence_digest_conflict", message: error.message });
        return;
      }
      res.status(400).json({ error: "invalid_evidence_batch", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get("/run-evidence", async (req: Request, res: Response) => {
    const flowId = textOrNull(req.query.flowId ?? req.query.flow_id);
    if (!flowId) { res.status(400).json({ error: "Bad Request", message: "flowId is required" }); return; }
    const afterId = req.query.afterId == null ? undefined : Number(req.query.afterId);
    const maxId = req.query.maxId == null ? undefined : Number(req.query.maxId);
    const limit = positiveInt(req.query.limit, 1_000, 5_000);
    res.json({ flowId, events: await workflowEvolutionRepo.listEvidence(flowId, { afterId, maxId, limit }) });
  });

  router.post("/analysis-runs", async (req: Request, res: Response) => {
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const scope = body.scope && typeof body.scope === "object" && !Array.isArray(body.scope)
        ? body.scope as Record<string, unknown>
        : null;
      if (!scope) throw new Error("scope is invalid");
      const scopeType = String(body.scopeType ?? "single_run");
      if (!new Set(["single_run", "run_set", "workflow_window", "global_window"]).has(scopeType)) {
        throw new Error("scopeType is invalid");
      }
      const analysis = await workflowEvolutionRepo.createAnalysisRun({
        analysisId: requiredBodyText(body.analysisId ?? `AN-${crypto.randomUUID().replaceAll("-", "").slice(0, 20).toUpperCase()}`, "analysisId", 64),
        requestKey: requiredBodyText(body.requestKey, "requestKey", 64),
        scopeType: scopeType as "single_run" | "run_set" | "workflow_window" | "global_window",
        scope,
        flowId: textOrNull(body.flowId),
        workflowId: textOrNull(body.workflowId),
        analysisVersion: requiredBodyText(body.analysisVersion, "analysisVersion", 64),
        requestedBy: textOrNull(body.requestedBy) ?? (getActor(req) || null),
        requestedAtMs: Number.isSafeInteger(Number(body.requestedAtMs)) ? Number(body.requestedAtMs) : Date.now(),
        taskId: textOrNull(body.taskId),
        stepId: textOrNull(body.stepId),
      });
      res.status(201).json({ analysis });
    } catch (error) {
      res.status(400).json({ error: "invalid_analysis_request", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get("/analysis-runs/latest", async (req: Request, res: Response) => {
    const flowId = textOrNull(req.query.flowId ?? req.query.flow_id);
    if (!flowId) { res.status(400).json({ error: "Bad Request", message: "flowId is required" }); return; }
    const analysis = await workflowEvolutionRepo.findLatestAnalysisRunByFlow(flowId);
    if (!analysis) { res.status(404).json({ error: "analysis_not_found" }); return; }
    res.json({ analysis });
  });

  router.get("/analysis-runs/:analysisId", async (req: Request, res: Response) => {
    const analysis = await workflowEvolutionRepo.findAnalysisRun(String(req.params.analysisId));
    if (!analysis) { res.status(404).json({ error: "analysis_not_found" }); return; }
    res.json({ analysis });
  });

  router.get("/analysis-runs/:analysisId/input", async (req: Request, res: Response) => {
    const input = await workflowEvolutionRepo.getAnalysisInput(String(req.params.analysisId));
    if (!input) { res.status(404).json({ error: "analysis_not_found" }); return; }
    let workflowSpecDigest: string | null = null;
    let workflowSpec: Record<string, unknown> | null = null;
    if (input.analysis.workflow_id) {
      const row = (await db.query<{ spec_json: string }>(
        "SELECT spec_json FROM workflow_specs WHERE workflow_id = ? LIMIT 1",
        [input.analysis.workflow_id],
      ).catch(() => []))[0];
      if (row?.spec_json) {
        try {
          const { digestCanonicalJson } = await import("../../services/evolution/contracts.js");
          const parsed = JSON.parse(row.spec_json) as unknown;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            workflowSpec = parsed as Record<string, unknown>;
            workflowSpecDigest = digestCanonicalJson(workflowSpec);
          }
        } catch {
          workflowSpec = null;
          workflowSpecDigest = null;
        }
      }
    }
    res.json({ ...input, workflowSpecDigest, workflowSpec });
  });

  router.post("/analysis-runs/:analysisId/claim", async (req: Request, res: Response) => {
    try {
      const expectedVersion = Number((req.body as { expectedVersion?: unknown } | undefined)?.expectedVersion);
      if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 0) throw new Error("expectedVersion is invalid");
      const analysisId = String(req.params.analysisId);
      const analysis = await workflowEvolutionRepo.findAnalysisRun(analysisId);
      if (!analysis) { res.status(404).json({ error: "analysis_not_found" }); return; }
      await assertLinkedAnalysisBot(analysis, textOrNull((req.body as { botId?: unknown } | undefined)?.botId));
      res.json({ analysis: await workflowEvolutionRepo.markAnalyzing(analysisId, expectedVersion) });
    } catch (error) {
      const status = error instanceof AnalysisBotMismatchError ? 403 : error instanceof WorkflowAnalysisStateConflictError ? 409 : 400;
      res.status(status).json({ error: status === 403 ? "analysis_bot_mismatch" : status === 409 ? "analysis_state_conflict" : "invalid_analysis_claim", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/analysis-runs/:analysisId/progress", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const analysisId = String(req.params.analysisId);
      const body = (req.body ?? {}) as Record<string, unknown>;
      const analysis = await workflowEvolutionRepo.findAnalysisRun(analysisId);
      if (!analysis) { res.status(404).json({ error: "analysis_not_found" }); return; }
      await assertLinkedAnalysisBot(analysis, textOrNull(body.botId));
      if (!analysis.step_id) throw new Error("analysis is not linked to a progress step");
      if (analysis.status !== "analyzing") {
        res.json({ ok: true, ignored: true, terminal: ["completed", "failed"].includes(analysis.status) });
        return;
      }
      const phase = textOrNull(body.phase) as AnalysisProgressPhase | null;
      const message = textOrNull(body.message);
      const elapsedMs = Number(body.elapsedMs);
      if (!phase || !ANALYSIS_PROGRESS_PHASES.includes(phase) || !message || message.length > 500
        || !Number.isFinite(elapsedMs) || elapsedMs < 0) {
        throw new Error("analysis progress is invalid");
      }
      const step = await evolveRepo.findStep(analysis.step_id);
      if (!step) throw new Error(`linked run_analysis step is invalid: ${analysis.step_id}`);
      let previousPhase: AnalysisProgressPhase | null = null;
      try {
        const output = JSON.parse(step.output_json ?? "null") as { analysisProgress?: { phase?: unknown } } | null;
        const value = output?.analysisProgress?.phase;
        if (typeof value === "string" && ANALYSIS_PROGRESS_PHASES.includes(value as AnalysisProgressPhase)) previousPhase = value as AnalysisProgressPhase;
      } catch { /* invalid historical output is replaced by current progress */ }
      if (previousPhase && ANALYSIS_PROGRESS_PHASES.indexOf(previousPhase) > ANALYSIS_PROGRESS_PHASES.indexOf(phase)) {
        res.json({ ok: true, ignored: true, phase: previousPhase });
        return;
      }
      const inputSummary = body.inputSummary && typeof body.inputSummary === "object" && !Array.isArray(body.inputSummary)
        ? body.inputSummary as Record<string, unknown>
        : undefined;
      const updatedAtMs = Date.now();
      await evolveRepo.updateStepStatus(analysis.step_id, {
        status: "analyzing",
        summary: message,
        output: { analysisProgress: { phase, message, elapsedMs, updatedAtMs, ...(inputSummary ? { inputSummary } : {}) } },
      });
      res.json({ ok: true, phase, updatedAtMs });
    } catch (error) {
      const status = error instanceof AnalysisBotMismatchError ? 403 : 400;
      res.status(status).json({ error: status === 403 ? "analysis_bot_mismatch" : "invalid_analysis_progress", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/analysis-runs/:analysisId/complete", async (req: Request, res: Response) => {
    try {
      const analysisId = String(req.params.analysisId);
      const existing = await workflowEvolutionRepo.findAnalysisRun(analysisId);
      if (!existing) { res.status(404).json({ error: "analysis_not_found" }); return; }
      await assertLinkedAnalysisBot(existing, textOrNull((req.body as { botId?: unknown } | undefined)?.botId));
      if (existing.status !== "analyzing" && existing.status !== "completed") {
        throw new WorkflowAnalysisStateConflictError("analysis must be claimed before completion");
      }
      const result = (req.body as { result?: unknown } | undefined)?.result;
      let duplicate = false;
      let analysis: WorkflowEvolutionAnalysisRow;
      try {
        analysis = await workflowEvolutionRepo.completeAnalysisRun(analysisId, result, Date.now());
      } catch (error) {
        if (!(error instanceof WorkflowAnalysisStateConflictError)) throw error;
        const current = await workflowEvolutionRepo.findAnalysisRun(analysisId);
        const validated = validateWorkflowEvolutionAnalysisResult(result);
        if (!current || current.status !== "completed"
          || current.result_digest !== digestCanonicalJson(validated)) throw error;
        analysis = current;
        duplicate = true;
      }
      if (analysis.flow_id) {
        await db.exec(
          "UPDATE flow_runs SET evolution_analysis_status = 'completed', evolution_analyzed_at = ? WHERE flow_id = ?",
          [db.dialect.now(), analysis.flow_id],
        ).catch(() => ({ affectedRows: 0 }));
      }
      try {
        await settleLinkedAnalysisTask(analysis, "completed");
      } catch (error) {
        console.error(`[internal/evolve] failed to settle linked task for ${analysisId}:`, error);
        res.status(503).json({
          error: "analysis_task_settlement_failed",
          message: error instanceof Error ? error.message : String(error),
          retryable: true,
        });
        return;
      }
      res.json({ analysis, ...(duplicate ? { duplicate: true } : {}) });
    } catch (error) {
      const status = error instanceof AnalysisBotMismatchError ? 403 : error instanceof WorkflowAnalysisStateConflictError ? 409 : 400;
      res.status(status).json({ error: status === 403 ? "analysis_bot_mismatch" : status === 409 ? "analysis_state_conflict" : "invalid_analysis_result", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/analysis-runs/:analysisId/fail", async (req: Request, res: Response) => {
    try {
      const errorCode = requiredBodyText((req.body as { errorCode?: unknown } | undefined)?.errorCode, "errorCode", 64);
      const analysisId = String(req.params.analysisId);
      const existing = await workflowEvolutionRepo.findAnalysisRun(analysisId);
      if (!existing) { res.status(404).json({ error: "analysis_not_found" }); return; }
      await assertLinkedAnalysisBot(existing, textOrNull((req.body as { botId?: unknown } | undefined)?.botId));
      if (existing.status !== "analyzing" && existing.status !== "failed") {
        throw new WorkflowAnalysisStateConflictError("analysis must be claimed before failure reporting");
      }
      let duplicate = false;
      let analysis: WorkflowEvolutionAnalysisRow;
      try {
        analysis = await workflowEvolutionRepo.failAnalysisRun(analysisId, errorCode, Date.now());
      } catch (error) {
        if (!(error instanceof WorkflowAnalysisStateConflictError)) throw error;
        const current = await workflowEvolutionRepo.findAnalysisRun(analysisId);
        if (!current || current.status !== "failed" || current.error_code !== errorCode) throw error;
        analysis = current;
        duplicate = true;
      }
      if (analysis.flow_id) {
        await db.exec(
          "UPDATE flow_runs SET evolution_analysis_status = 'failed', evolution_analyzed_at = ? WHERE flow_id = ?",
          [db.dialect.now(), analysis.flow_id],
        ).catch(() => ({ affectedRows: 0 }));
      }
      try {
        await settleLinkedAnalysisTask(analysis, "failed");
      } catch (error) {
        console.error(`[internal/evolve] failed to settle linked task for ${analysisId}:`, error);
        res.status(503).json({
          error: "analysis_task_settlement_failed",
          message: error instanceof Error ? error.message : String(error),
          retryable: true,
        });
        return;
      }
      res.json({ analysis, ...(duplicate ? { duplicate: true } : {}) });
    } catch (error) {
      const status = error instanceof AnalysisBotMismatchError ? 403 : error instanceof WorkflowAnalysisStateConflictError ? 409 : 400;
      res.status(status).json({ error: status === 403 ? "analysis_bot_mismatch" : status === 409 ? "analysis_state_conflict" : "invalid_analysis_failure", message: error instanceof Error ? error.message : String(error) });
    }
  });

  async function queryLangfuseTraces(sessionKeys: string[]): Promise<Row[]> {
    if (sessionKeys.length === 0) return [];
    const placeholders = sessionKeys.map(() => "?").join(",");
    try {
      return await db.query<Row>(
        `SELECT trace_id, name, session_id, real_session_id, gmt_trace,
                input, output, metadata, latency, total_cost, user_id
         FROM aw_langfuse_traces
         WHERE session_id IN (${placeholders}) OR real_session_id IN (${placeholders})
         ORDER BY gmt_trace DESC LIMIT 100`,
        [...sessionKeys, ...sessionKeys],
      );
    } catch {
      return [];
    }
  }

  async function queryLangfuseObservations(traceIds: string[]): Promise<Row[]> {
    if (traceIds.length === 0) return [];
    const placeholders = traceIds.map(() => "?").join(",");
    try {
      return await db.query<Row>(
        `SELECT observation_id, trace_id, parent_observation_id, type, name,
                start_time, end_time, input, output, model, status_message,
                usage_input_tokens, usage_output_tokens, usage_total_tokens, latency
         FROM aw_langfuse_observation
         WHERE trace_id IN (${placeholders})
         ORDER BY start_time ASC`,
        traceIds,
      );
    } catch {
      return [];
    }
  }

  /**
   * GET /run-logs?flowId=...
   *
   * 返回一个 run 的完整原料：flow_runs + node_executions + node_step_traces。
   * 供 bot agent 分析使用。
   */
  router.get("/run-logs", async (req: Request, res: Response) => {
    try {
      const flowId = textOrNull(req.query.flowId ?? req.query.flow_id);
      if (!flowId) {
        res.status(400).json({ error: "Bad Request", message: "flowId is required" });
        return;
      }
      if (!flowRunRepo || !nodeExecRepo || !nodeStepTraceRepo) {
        res.status(503).json({ error: "Service Unavailable", message: "Required repositories not configured" });
        return;
      }

      const [flow, nodes, traces] = await Promise.all([
        flowRunRepo.findFullByFlowId(flowId),
        nodeExecRepo.findByFlowId(flowId, { limit: 1000 }),
        nodeStepTraceRepo.findByFlowId(flowId, { limit: 1000 }),
      ]);

      if (!flow) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      res.json({ ok: true, flowId, flow, nodes, traces });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /run-logs failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * GET /run-archive?flowId=...
   *
   * 返回一个 run 的完整归档：flow_runs + node_executions + flow_events +
   * node_step_traces + execution_step_log + run_logs + Langfuse traces/observations。
   * 供 bot agent 做全链路根因分析。
   */
  router.get("/run-archive", async (req: Request, res: Response) => {
    try {
      const flowId = textOrNull(req.query.flowId ?? req.query.flow_id);
      if (!flowId) {
        res.status(400).json({ error: "Bad Request", message: "flowId is required" });
        return;
      }
      if (!flowRunRepo || !nodeExecRepo || !nodeStepTraceRepo) {
        res.status(503).json({ error: "Service Unavailable", message: "Required repositories not configured" });
        return;
      }

      const [flow, nodes, flowEvents, stepTraces, stepLogs, runLogs] = await Promise.all([
        flowRunRepo.findFullByFlowId(flowId),
        nodeExecRepo.findByFlowId(flowId, { limit: 1000 }),
        db.query<Row>("SELECT * FROM flow_events WHERE flow_id = ? ORDER BY time", [flowId]).catch((): Row[] => []),
        nodeStepTraceRepo.findByFlowId(flowId, { limit: 1000 }),
        executionStepLogRepo ? executionStepLogRepo.getStepsByFlow(flowId, { limit: 1000 }) : Promise.resolve([]),
        runLogRepo ? runLogRepo.findByFlowId(flowId) : Promise.resolve([]),
      ]);

      if (!flow) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const sessionKeys = nodes
        .map((ne) => ne.embedded_session_key as string | null)
        .filter((k): k is string => !!k);
      const langfuseTraces = await queryLangfuseTraces(sessionKeys);
      const traceIds = langfuseTraces.map((t) => t.trace_id as string).filter(Boolean);
      const langfuseObservations = await queryLangfuseObservations(traceIds);

      res.json({
        ok: true,
        flowId,
        flow,
        nodes,
        flowEvents,
        stepTraces,
        stepLogs,
        runLogs,
        langfuseTraces,
        langfuseObservations,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /run-archive failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * GET /run-archive/timeline?flowId=...
   *
   * 返回一个 run 的统一运行时间线，供产品 UI 和 bot inspect 使用。
   * 聚合：flow_events + node_step_traces + execution_step_log + run_logs。
   */
  router.get("/run-archive/timeline", async (req: Request, res: Response) => {
    try {
      const flowId = textOrNull(req.query.flowId ?? req.query.flow_id);
      if (!flowId) {
        res.status(400).json({ error: "Bad Request", message: "flowId is required" });
        return;
      }
      if (!flowRunRepo || !nodeExecRepo || !nodeStepTraceRepo) {
        res.status(503).json({ error: "Service Unavailable", message: "Required repositories not configured" });
        return;
      }

      const [flow, flowEvents, stepTraces, stepLogs, runLogs] = await Promise.all([
        flowRunRepo.findFullByFlowId(flowId),
        db.query<Row>("SELECT * FROM flow_events WHERE flow_id = ? ORDER BY time", [flowId]).catch((): Row[] => []),
        nodeStepTraceRepo.findByFlowId(flowId, { limit: 1000 }),
        executionStepLogRepo ? executionStepLogRepo.getStepsByFlow(flowId, { limit: 1000 }) : Promise.resolve([]),
        runLogRepo ? runLogRepo.findByFlowId(flowId) : Promise.resolve([]),
      ]);

      if (!flow) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const timeline = buildRunTimeline({ flowId, flow, flowEvents, stepTraces, stepLogs, runLogs });
      res.json(timeline);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /run-archive/timeline failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * POST /diagnoses
   *
   * Bot 回写单条诊断。
   */
  router.post("/diagnoses", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const b = req.body as Record<string, unknown>;
      const flowId = String(b.flow_id ?? b.flowId ?? "");
      const workflowId = String(b.workflow_id ?? b.workflowId ?? "");
      if (!flowId || !workflowId) {
        res.status(400).json({ error: "Bad Request", message: "flowId and workflowId are required" });
        return;
      }

      const diagnosis = await evolveRepo.createDiagnosis({
        diagnosisId: textOrNull(b.diagnosis_id ?? b.diagnosisId) ?? `DG-${crypto.randomUUID().slice(0, 12).toUpperCase()}`,
        flowId,
        workflowId,
        runId: textOrNull(b.run_id ?? b.runId) ?? flowId,
        nodeId: textOrNull(b.node_id ?? b.nodeId),
        failureSignature: String(b.failure_signature ?? b.failureSignature ?? ""),
        failureMode: String(b.failure_mode ?? b.failureMode ?? "other"),
        executorType: textOrNull(b.executor_type ?? b.executorType),
        weakNodeId: textOrNull(b.weak_node_id ?? b.weakNodeId),
        suggestedFixKind: textOrNull(b.suggested_fix_kind ?? b.suggestedFixKind) ?? "retry-as-is",
        lessonIdHit: textOrNull(b.lesson_id_hit ?? b.lessonIdHit),
        errorText: textOrNull(b.error_text ?? b.errorText),
      });

      res.status(201).json({ ok: true, diagnosis });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] POST /diagnoses failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * POST /lessons
   *
   * Bot 回写经验（通常是 draft lesson）。
   */
  router.post("/lessons", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const b = req.body as Record<string, unknown>;
      const failureSignature = String(b.failure_signature ?? b.failureSignature ?? "");
      if (!failureSignature) {
        res.status(400).json({ error: "Bad Request", message: "failureSignature is required" });
        return;
      }

      const lesson = await evolveRepo.createLesson({
        lessonId: textOrNull(b.lesson_id ?? b.lessonId) ?? `LS-${crypto.randomUUID().slice(0, 12).toUpperCase()}`,
        workflowId: textOrNull(b.workflow_id ?? b.workflowId),
        nodeId: textOrNull(b.node_id ?? b.nodeId),
        failureSignature,
        failureMode: String(b.failure_mode ?? b.failureMode ?? "other"),
        executorType: textOrNull(b.executor_type ?? b.executorType),
        fixKind: String(b.fix_kind ?? b.fixKind ?? "retry-as-is"),
        fixSpec: String(b.fix_spec ?? b.fixSpec ?? ""),
        status: textOrNull(b.status) ?? "draft",
        source: textOrNull(b.source) ?? "log_analysis",
        note: textOrNull(b.note),
        confidence: typeof b.confidence === "number" ? b.confidence : undefined,
      });

      res.status(201).json({ ok: true, lesson });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] POST /lessons failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * POST /outcomes
   *
   * Bot 回写 lesson outcome（命中/应用/救回）。
   */
  router.post("/outcomes", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const b = req.body as Record<string, unknown>;
      const lessonId = textOrNull(b.lesson_id ?? b.lessonId);
      if (!lessonId) {
        res.status(400).json({ error: "Bad Request", message: "lessonId is required" });
        return;
      }

      const outcome = await evolveRepo.recordLessonOutcome({
        outcomeId: textOrNull(b.outcome_id ?? b.outcomeId) ?? `OC-${crypto.randomUUID().slice(0, 12).toUpperCase()}`,
        lessonId,
        workflowId: textOrNull(b.workflow_id ?? b.workflowId),
        nodeId: textOrNull(b.node_id ?? b.nodeId),
        action: String(b.action ?? ""),
        applied: b.applied === true || b.applied === 1,
        succeeded: b.succeeded === true || b.succeeded === 1,
        verdict: textOrNull(b.verdict) ?? String(b.verdict ?? ""),
        note: textOrNull(b.note),
      });

      res.status(201).json({ ok: true, outcome });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] POST /outcomes failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * GET /diagnoses
   *
   * Bot 查询某 workflow 的历史诊断。
   */
  router.get("/diagnoses", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const workflowId = textOrNull(req.query.workflowId ?? req.query.workflow_id);
      const query = textOrNull(req.query.query);
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const result = await workflowEvolutionRepo.listProjectedDiagnoses({ workflowId: workflowId ?? undefined, query: query ?? undefined, limit, offset });
      res.json({ diagnoses: result.rows, total: result.total, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /diagnoses failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * GET /lessons
   *
   * Bot 查询经验库。
   */
  router.get("/lessons", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const workflowId = textOrNull(req.query.workflowId ?? req.query.workflow_id);
      const query = textOrNull(req.query.query);
      const status = textOrNull(req.query.status);
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const result = await evolveRepo.listLessons({ workflowId, status, query, limit, offset });
      res.json({ lessons: result.rows, total: result.total, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /lessons failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * POST /mark-analyzed
   *
   * Bot 更新 run 的分析状态（analyzing / completed / failed）。
   */
  router.post("/mark-analyzed", async (req: Request, res: Response) => {
    try {
      if (!flowRunRepo) {
        res.status(503).json({ error: "Service Unavailable", message: "FlowRunRepository not configured" });
        return;
      }
      const b = req.body as Record<string, unknown>;
      const flowId = textOrNull(b.flow_id ?? b.flowId);
      const status = textOrNull(b.status);
      if (!flowId || !status) {
        res.status(400).json({ error: "Bad Request", message: "flowId and status are required" });
        return;
      }
      const ok = await flowRunRepo.updateAnalysisStatus(flowId, status);
      res.json({ ok });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] POST /mark-analyzed failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * POST /suggestions
   *
   * Bot 回写建议（分析日志后生成）。
   */
  router.post("/suggestions", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const b = req.body as Record<string, unknown>;
      const workflowId = String(b.workflow_id ?? b.workflowId ?? "");
      const failureSignature = String(b.failure_signature ?? b.failureSignature ?? "");
      if (!workflowId || !failureSignature) {
        res.status(400).json({ error: "Bad Request", message: "workflowId and failureSignature are required" });
        return;
      }

      const sourceDiagnosisIds = Array.isArray(b.source_diagnosis_ids)
        ? (b.source_diagnosis_ids as string[])
        : Array.isArray(b.sourceDiagnosisIds)
          ? (b.sourceDiagnosisIds as string[])
          : [];
      const impactRunIds = Array.isArray(b.impact_run_ids)
        ? (b.impact_run_ids as string[])
        : Array.isArray(b.impactRunIds)
          ? (b.impactRunIds as string[])
          : [];

      const suggestion = await evolveRepo.createSuggestion({
        workflowId,
        nodeId: textOrNull(b.node_id ?? b.nodeId),
        weakNodeId: textOrNull(b.weak_node_id ?? b.weakNodeId),
        failureSignature,
        failureMode: textOrNull(b.failure_mode ?? b.failureMode),
        fixKind: textOrNull(b.fix_kind ?? b.fixKind),
        fixSpec: typeof b.fix_spec === "string" ? b.fix_spec : typeof b.fixSpec === "string" ? b.fixSpec : "",
        sourceDiagnosisIds,
        impactRunIds,
        status: textOrNull(b.status) ?? "pending",
      });

      res.status(201).json({ ok: true, suggestion });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] POST /suggestions failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * GET /suggestions
   *
   * Bot 查询建议列表（支持按 workflowId / status 过滤）。
   */
  router.get("/suggestions", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const workflowId = textOrNull(req.query.workflowId ?? req.query.workflow_id);
      const status = textOrNull(req.query.status);
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const result = await evolveRepo.listSuggestions({ workflowId, status, limit, offset });
      res.json({ suggestions: result.rows, total: result.total, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /suggestions failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * GET /suggestions/:id
   *
   * Bot 查询单条建议详情。
   */
  router.get("/suggestions/:suggestionId", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const suggestion = await evolveRepo.findSuggestionById(String(req.params.suggestionId));
      if (!suggestion) {
        res.status(404).json({ error: "Not Found", message: "Suggestion not found" });
        return;
      }
      res.json({ suggestion });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] GET /suggestions/:id failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /**
   * POST /suggestions/:id/action
   *
   * Bot 标记建议状态（adopted/applied/rejected/verified/pending）。
   */
  router.post("/suggestions/:suggestionId/action", async (req: Request, res: Response) => {
    try {
      assertRepo(evolveRepo);
      const suggestion = await evolveRepo.findSuggestionById(String(req.params.suggestionId));
      if (!suggestion) {
        res.status(404).json({ error: "Not Found", message: "Suggestion not found" });
        return;
      }
      const body = (req.body ?? {}) as Record<string, unknown>;
      const action = textOrNull(body.action);
      const statusMap: Record<string, string> = {
        adopt: "adopted", adopted: "adopted",
        apply: "applied_unverified", applied: "applied_unverified", applied_unverified: "applied_unverified",
        reject: "rejected", rejected: "rejected",
        verify: "verified", verified: "verified", ineffective: "ineffective",
        bench: "benched", benched: "benched",
        pending: "pending",
      };
      if (!action || !statusMap[action]) {
        res.status(400).json({ error: "Bad Request", message: "action must be one of adopted/applied/rejected/verified/benched/pending" });
        return;
      }
      const nextStatus = statusMap[action];
      const actor = getActor(req) || textOrNull(body.actor) || "system";
      const note = typeof body.note === "string" ? body.note : null;
      const updated = nextStatus === "applied_unverified"
        ? await evolveRepo.markSuggestionAppliedUnverified(suggestion.id, { actor, note })
        : nextStatus === "verified" || nextStatus === "ineffective"
          ? await evolveRepo.markSuggestionVerification(suggestion.id, nextStatus, { actor, note })
          : await evolveRepo.updateSuggestionStatus(suggestion.id, nextStatus, {
            action,
            actor,
            note,
            timestamp: new Date().toISOString(),
          });
      if (!updated) {
        throw new Error("更新 suggestion 状态失败");
      }
      await evolveRepo.updateDiagnosesSuggestionStatus(suggestion.workflow_id, suggestion.failure_signature, String(suggestion.id), nextStatus);
      const recorded = await evolveRepo.recordSuggestionAction({
        workflowId: suggestion.workflow_id,
        signature: suggestion.failure_signature,
        action: nextStatus,
        nodeId: suggestion.node_id,
        fixKind: suggestion.fix_kind,
        note,
        createdBy: actor,
      });
      res.json({ suggestion: updated, action: recorded });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[internal/evolve] POST /suggestions/:id/action failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}
