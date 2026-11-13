import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { effectResumeTiming, recoveryEffectWait, type EffectResumePolicy } from "./effect-resume.js";
import { SessionRecoveryError } from "./errors.js";
import { validateRecoveryEvidenceReferences } from "./task-state.js";
import type { RecoveryJudgment, RecoveryStoredTask } from "./task-contracts.js";

export const effectRecheckReadTools = new Set(["session_read", "trajectory_read", "runtime_inspect", "logs_search"]);
export const definitiveEffect = (effect: unknown) => effect === "recovered" || effect === "not_recovered";

export function effectRecheckPhase(task: Pick<RecoveryStoredTask, "phase" | "timeline">): "observe" | "verify" {
  return task.phase === "verify" || task.timeline.some(event => event.kind === "operation" && event.payload.tool_name === "apply_action")
    || task.phase === "apply" ? "verify" : "observe";
}

export function recheckUnavailable(task: RecoveryStoredTask, policy: EffectResumePolicy | null, now: number, ownExecutionId?: string): string | undefined {
  if (task.simulation || task.level === "debug" || task.judgment?.effect === "not_intervened" || task.completionReason === "no_recovery_needed") return "not_a_live_intervention";
  if (task.effectResumeSlot || (task.execution && task.execution.state !== "ended" && task.execution.executionId !== ownExecutionId)
    || ((task.executionRetry?.retryAt ?? 0) > 0 && task.status === "running")) return "execution_pending";
  if (task.status === "waiting_effect") {
    if (!policy || !["observe", "verify"].includes(task.phase)) return;
    const wait = recoveryEffectWait(task);
    if (!wait) return "wait_history_unverified";
    if (effectResumeTiming(wait, now, policy).remaining) return "online_resume_available";
  } else if (!["failed", "canceled", "completed"].includes(task.status)) return "online_task";
}

export function validateEffectRecheckJudgment(task: RecoveryStoredTask, judgment: RecoveryJudgment, freshEvidence: ReadonlySet<string>) {
  const invalid = (message: string): never => { throw new SessionRecoveryError(422, "invalid_effect_recheck", message); };
  if (!judgment.summary.trim() || judgment.summary.length > 8000 || containsSecret(judgment.summary)
    || judgment.evidence_ids.length > 100 || judgment.evidence_ids.some(id => typeof id !== "string")) invalid("补验摘要或证据引用无效");
  validateRecoveryEvidenceReferences(task, judgment.evidence_ids);
  if (definitiveEffect(judgment.effect)) {
    if (task.interventionStatus !== "performed") invalid("尚未核实原任务实际发生过干预；请补读原始证据，无法核实则提交 unknown");
    if (!judgment.evidence_ids.some(id => freshEvidence.has(id))) invalid("确定的效果结论必须引用本次补验成功读取的现场证据");
  }
}

export function recheckError(reason: string, status = 409): never {
  throw new SessionRecoveryError(status, reason, "当前任务不满足离线补验条件");
}
