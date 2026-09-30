/**
 * ExecutionStepLogger — structured event logger for dynamic workflow execution.
 *
 * Receives high-level execution events (node start/complete/fail, node
 * materialization, LLM evaluation, budget checks, etc.) and writes them
 * to the execution_step_log table via IExecutionStepLogRepository.
 *
 * Responsibilities:
 * - Truncates input/output summaries to 4KB with metadata markers
 * - Serializes LLM evaluation details and decision paths as JSON
 * - Best-effort writes: repository failure is logged but never throws
 */
import type {
  IExecutionStepLogRepository,
  ExecutionStepLogInsert,
  ExecutionStepType,
} from "../db/repositories/types.js";

// ── Truncation ──

/** Maximum size for input_summary / output_summary JSON strings (bytes). */
const SUMMARY_MAX_BYTES = 4 * 1024;

/** Preview portion kept when truncating (3.5KB leaves room for wrapper JSON). */
const PREVIEW_MAX_BYTES = 3.5 * 1024;

/**
 * Truncate a JSON string if it exceeds maxBytes.
 * Returns a truncated wrapper: { _truncated: true, _originalSize, preview }
 * If under the limit, returns the original string unchanged.
 */
function truncateSummary(jsonStr: string | null | undefined): string | null {
  if (!jsonStr) return null;

  const byteLength = Buffer.byteLength(jsonStr, "utf8");
  if (byteLength <= SUMMARY_MAX_BYTES) return jsonStr;

  // Find a safe cut point within the preview budget
  let cutIndex = jsonStr.length;
  while (Buffer.byteLength(jsonStr.substring(0, cutIndex), "utf8") > PREVIEW_MAX_BYTES && cutIndex > 0) {
    cutIndex = Math.floor(cutIndex / 2);
  }

  const preview = jsonStr.substring(0, cutIndex);
  return JSON.stringify({
    _truncated: true,
    _originalSize: byteLength,
    preview,
  });
}

/**
 * Safely serialize a value to JSON string, then truncate if needed.
 * Returns null for null/undefined input.
 */
function serializeAndTruncate(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const jsonStr = typeof value === "string" ? value : JSON.stringify(value);
  return truncateSummary(jsonStr);
}

// ── Public types ──

export type StepLogEntry = {
  flowId: string;
  nodeId: string;
  stepType: ExecutionStepType;
  timestamp?: number;
  inputSummary?: unknown;
  outputSummary?: unknown;
  llmEvaluation?: unknown;
  decisionPath?: unknown;
  durationMs?: number | null;
  tokenUsage?: number | null;
  metadata?: Record<string, unknown> | null;
};

// ── Logger ──

export class ExecutionStepLogger {
  constructor(private repo: IExecutionStepLogRepository) {}

  /**
   * Log an execution step event.
   * Best-effort: repository failure is logged but never throws.
   */
  async log(entry: StepLogEntry): Promise<void> {
    try {
      const insert: ExecutionStepLogInsert = {
        flowId: entry.flowId,
        nodeId: entry.nodeId,
        stepType: entry.stepType,
        timestamp: entry.timestamp ?? Date.now(),
        inputSummary: serializeAndTruncate(entry.inputSummary),
        outputSummary: serializeAndTruncate(entry.outputSummary),
        llmEvaluation: entry.llmEvaluation != null
          ? (typeof entry.llmEvaluation === "string"
            ? entry.llmEvaluation
            : JSON.stringify(entry.llmEvaluation))
          : null,
        decisionPath: entry.decisionPath != null
          ? (typeof entry.decisionPath === "string"
            ? entry.decisionPath
            : JSON.stringify(entry.decisionPath))
          : null,
        durationMs: entry.durationMs ?? null,
        tokenUsage: entry.tokenUsage ?? null,
        metadata: entry.metadata ?? null,
      };

      await this.repo.insertStep(insert);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[execution-log] ExecutionStepLogger.log failed: ${msg}`);
    }
  }

  /**
   * Convenience: log a node start event.
   */
  async logStart(flowId: string, nodeId: string, inputSummary?: unknown): Promise<void> {
    await this.log({ flowId, nodeId, stepType: "start", inputSummary });
  }

  /**
   * Convenience: log a node complete event.
   */
  async logComplete(
    flowId: string,
    nodeId: string,
    durationMs: number,
    outputSummary?: unknown,
    tokenUsage?: number,
  ): Promise<void> {
    await this.log({
      flowId,
      nodeId,
      stepType: "complete",
      durationMs,
      outputSummary,
      tokenUsage: tokenUsage ?? null,
    });
  }

  /**
   * Convenience: log a node fail event.
   */
  async logFail(
    flowId: string,
    nodeId: string,
    durationMs: number,
    errorSummary?: unknown,
  ): Promise<void> {
    await this.log({
      flowId,
      nodeId,
      stepType: "fail",
      durationMs,
      outputSummary: errorSummary,
    });
  }

  /**
   * Convenience: log a node materialization event (dynamic-template).
   */
  async logMaterialize(
    flowId: string,
    nodeId: string,
    decisionPath: { templateName: string; sourceNodeId: string; index: number },
  ): Promise<void> {
    await this.log({
      flowId,
      nodeId,
      stepType: "materialize",
      decisionPath,
    });
  }

  /**
   * Convenience: log a node injection event (LLM orchestrator).
   */
  async logInject(
    flowId: string,
    nodeId: string,
    decisionPath: { action: string; reason: string; stepNum: number; params?: Record<string, unknown> },
  ): Promise<void> {
    await this.log({
      flowId,
      nodeId,
      stepType: "inject",
      decisionPath,
    });
  }

  /**
   * Convenience: log an LLM evaluation event.
   */
  async logLlmEvaluate(
    flowId: string,
    nodeId: string,
    evaluation: {
      condition: string;
      result: string;
      reason: string;
      model: string;
      durationMs: number;
      tokenUsage?: number;
    },
  ): Promise<void> {
    await this.log({
      flowId,
      nodeId,
      stepType: "llm_evaluate",
      llmEvaluation: evaluation,
      durationMs: evaluation.durationMs,
      tokenUsage: evaluation.tokenUsage ?? null,
    });
  }

  /**
   * Convenience: log a budget check/warning/exhausted event.
   */
  async logBudgetEvent(
    flowId: string,
    nodeId: string,
    stepType: "budget_check" | "budget_warning" | "budget_exhausted",
    details: { budgetType: string; used: number; limit: number; ratio: number },
  ): Promise<void> {
    await this.log({
      flowId,
      nodeId,
      stepType,
      metadata: { budget: details },
    });
  }
}

// Export truncation helpers for testing
export { truncateSummary, serializeAndTruncate, SUMMARY_MAX_BYTES };