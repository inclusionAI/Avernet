import type { EvolveRepository } from "../../repositories/evolve-repository.js";

const MIN_EXECUTION_MS = 15 * 60_000;
const HEARTBEAT_GRACE_MS = 3 * 60_000;
const ABSOLUTE_TIMEOUT_MS = 60 * 60_000;
const SWEEP_INTERVAL_MS = 60_000;

function timestampMs(value: number | string | null | undefined): number {
  if (value == null) return 0;
  if (typeof value === "number" && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function classifySuggestionApplyTimeout(input: {
  nowMs: number;
  startedAtMs: number;
  updatedAtMs: number;
  phase: string | null;
}): { timedOut: false } | { timedOut: true; errorCode: string; message: string; resultUnknown: boolean } {
  const executionMs = Math.max(0, input.nowMs - input.startedAtMs);
  const silenceMs = Math.max(0, input.nowMs - input.updatedAtMs);
  const absolute = executionMs >= ABSOLUTE_TIMEOUT_MS;
  const inactive = executionMs >= MIN_EXECUTION_MS && silenceMs >= HEARTBEAT_GRACE_MS;
  if (!absolute && !inactive) return { timedOut: false };
  if (input.phase === "deploying") {
    return {
      timedOut: true,
      errorCode: "SUGGESTION_APPLY_RESULT_TIMEOUT",
      message: "部署阶段超时且未收到心跳，部署结果未知，请先核对 Workflow 当前版本再重试",
      resultUnknown: true,
    };
  }
  return {
    timedOut: true,
    errorCode: "SUGGESTION_APPLY_TIMEOUT",
    message: absolute
      ? "应用任务执行超过 60 分钟，已自动结束"
      : "应用任务执行超过 15 分钟且 3 分钟未收到心跳，已自动结束",
    resultUnknown: false,
  };
}

export async function runSuggestionApplyTimeoutSweep(repo: EvolveRepository, nowMs = Date.now()): Promise<number> {
  const steps = await repo.listActiveSuggestionApplySteps();
  let timedOut = 0;
  for (const step of steps) {
    if (step.started_at == null) continue;
    let config: Record<string, unknown> = {};
    let progress: Record<string, unknown> = {};
    try { config = JSON.parse(step.config_json) as Record<string, unknown>; } catch { /* handled as empty */ }
    try {
      const output = JSON.parse(step.output_json ?? "null") as { applicationProgress?: Record<string, unknown> } | null;
      progress = output?.applicationProgress ?? {};
    } catch { /* use step modification time */ }
    const decision = classifySuggestionApplyTimeout({
      nowMs,
      startedAtMs: timestampMs(step.started_at),
      updatedAtMs: timestampMs(progress.updatedAtMs as number | string | undefined) || timestampMs(step.gmt_modified),
      phase: typeof progress.phase === "string" ? progress.phase : null,
    });
    if (!decision.timedOut) continue;
    if (!await repo.tryTimeoutSuggestionApplyStep(step.step_id, decision.errorCode, decision.message)) continue;
    timedOut += 1;
    const suggestionIds = Array.isArray(config.suggestionIds)
      ? config.suggestionIds.map(String).filter(Boolean)
      : [String(config.suggestionId ?? "")].filter(Boolean);
    const workflowId = String(config.workflowId ?? "");
    for (const suggestionId of suggestionIds) {
      const suggestion = await repo.updateSuggestionStatus(suggestionId, "failed", {
        action: "failed",
        actor: "suggestion-apply-timeout-sweeper",
        note: decision.message,
        timestamp: new Date(nowMs).toISOString(),
      });
      if (suggestion && workflowId) {
        await repo.updateDiagnosesSuggestionStatus(workflowId, suggestion.failure_signature, suggestionId, "failed");
      }
      await repo.recordSuggestionOutcome({
        suggestionId,
        workflowId,
        nodeId: suggestion?.node_id ?? null,
        action: "suggestion_apply",
        applied: false,
        succeeded: false,
        verdict: decision.resultUnknown ? "application_result_unknown" : "application_timeout",
        note: decision.message,
        sourceTaskId: step.task_id,
        sourceStepId: step.step_id,
        createdBy: "suggestion-apply-timeout-sweeper",
      });
    }
  }
  return timedOut;
}

export function startSuggestionApplyTimeoutSweeper(repo: EvolveRepository): NodeJS.Timeout {
  const sweep = () => runSuggestionApplyTimeoutSweep(repo).catch((error) => {
    console.warn(`[clawweb][task-guard] suggestion apply timeout sweep failed: ${error instanceof Error ? error.message : String(error)}`);
  });
  void sweep();
  const timer = setInterval(sweep, SWEEP_INTERVAL_MS);
  timer.unref();
  return timer;
}
