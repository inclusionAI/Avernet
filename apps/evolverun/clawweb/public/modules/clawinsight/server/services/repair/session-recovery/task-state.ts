import { validateRecoveryAction } from "./action-scope.js";
import { createHash } from "node:crypto";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { SessionRecoveryError } from "./errors.js";
import type { RecoveryJudgment, RecoveryPlan, RecoveryStoredTask, RecoveryTaskResult, RecoveryTaskState } from "./task-contracts.js";

function conflict(message: string): never {
  throw new SessionRecoveryError(409, "recovery_state_conflict", message);
}
function invalid(message: string): never { throw new SessionRecoveryError(422, "invalid_recovery_result", message); }
function active(state: RecoveryTaskState): void {
  if (state.status !== "running") conflict("任务当前不在执行中");
}
function next(state: RecoveryTaskState, changes: Partial<RecoveryTaskState>): RecoveryTaskState {
  return { ...state, ...changes, version: state.version + 1 };
}

/** Observation timeout preserves the business phase; it never authorizes an environment repair. */
export function observeRecovery(state: RecoveryTaskState, judgment: RecoveryJudgment, now: number): RecoveryTaskState {
  active(state);
  if (!["observe", "verify"].includes(state.phase)) conflict("当前阶段不能提交效果判断");
  const evidenceIds = new Set(state.evidence.map(item => item.evidence_id));
  if (judgment.evidence_ids.some(id => !evidenceIds.has(id))) invalid("效果判断引用了未留存证据");
  if (judgment.effect !== "unknown" && !judgment.evidence_ids.length) invalid("确定效果结论必须有证据");
  if (state.level === "debug") conflict("Debug 应回报未实际干预，不能回报真实效果");
  if (judgment.effect === "not_intervened") conflict("实际干预任务不能以 Debug 结论收口");
  if (judgment.effect === "recovered") return next(state, { status: "completed", judgment });
  if (judgment.effect === "not_recovered") {
    return next(state, state.phase === "verify"
      ? { status: "failed", judgment } : { phase: "plan", judgment, observationDeadlineAt: null });
  }
  return next(state, { judgment, ...(state.observationDeadlineAt !== null && now >= state.observationDeadlineAt
    ? { status: "waiting_effect" as const } : {}) });
}

/** One complete environment plan. Owner requests belong to that plan. */
export function submitRecoveryPlan(state: RecoveryTaskState, plan: RecoveryPlan, now: number): RecoveryTaskState {
  active(state);
  if (state.phase !== "plan" || state.plan) conflict("本任务不能自动提交第二套修复方案");
  if (!plan || typeof plan !== "object" || [plan.summary, plan.basis, plan.scope, plan.verification].some(value => typeof value !== "string" || value.length > 8000)
    || !Array.isArray(plan.actions) || plan.actions.length > 50 || !Array.isArray(plan.owner_cooperation) || plan.owner_cooperation.length > 20
    || plan.owner_cooperation.some(item => !item || typeof item.request !== "string" || !item.request.trim() || typeof item.basis !== "string" || !item.basis.trim())) invalid("方案格式无效");
  for (const action of plan.actions) {
    if (!action || typeof action.actionId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(action.actionId)
      || [action.summary, action.risk, action.verification].some(value => typeof value !== "string" || !value.trim() || value.length > 8000)
      || action.type !== undefined && !["container_command", "engine_operation", "ocb_operation"].includes(action.type)
      || action.command !== undefined && (typeof action.command !== "string" || !action.command.trim() || action.command.length > 16384 || action.command.includes("\0"))
      || action.engineOperation !== undefined && (!action.engineOperation || typeof action.engineOperation.path !== "string" || !["GET", "POST", "PUT", "PATCH", "DELETE"].includes(action.engineOperation.method))
      || action.operation !== undefined && (!action.operation || typeof action.operation.type !== "string")
      || [action.command, action.engineOperation, action.operation].filter(value => value !== undefined).length > 1) invalid("方案操作格式无效");
    validateRecoveryAction(action);
  }
  if (!plan.summary.trim() || !plan.basis.trim() || !plan.scope.trim() || !plan.verification.trim()) invalid("方案必须说明依据、范围与验证方法");
  if (containsSecret(JSON.stringify(plan))) invalid("方案不得包含凭据");
  const ids = plan.actions.map(action => action.actionId);
  if (new Set(ids).size !== ids.length) invalid("方案 actionId 必须唯一");
  const digest = createHash("sha256").update(JSON.stringify(plan)).digest("hex");
  const requiresOwner = state.level !== "debug" && (state.level === "approval" || plan.owner_cooperation.length > 0);
  return next(state, {
    plan: { ...plan, digest, approvedAt: requiresOwner || state.level === "debug" ? null : now },
    status: requiresOwner ? "waiting_owner" : state.level === "debug" ? "completed" : "running",
    phase: state.simulation ? "plan" : "apply", ownerDeadlineAt: null,
    ...(state.level === "debug" ? { judgment: { delivery: "unconfirmed", response: "unknown", effect: "not_intervened", summary: "未实际干预", evidence_ids: [] } as RecoveryJudgment } : {}),
  });
}

export function decideRecoveryPlan(state: RecoveryTaskState, digest: string, approved: boolean, now: number): RecoveryTaskState {
  if (state.simulation) conflict("仿真方案不可批准执行");
  if (state.status !== "waiting_owner" || !state.plan || state.plan.digest !== digest) conflict("决策不对应当前待确认方案");
  return next(state, approved
    ? { status: "running", ownerDeadlineAt: null, plan: { ...state.plan, approvedAt: now } }
    : { status: "failed", ownerDeadlineAt: null,
        judgment: { delivery: state.judgment?.delivery ?? "unconfirmed", response: state.judgment?.response ?? "unknown",
          effect: state.judgment?.effect ?? "unknown", summary: "方案未获 Owner 批准，本次自愈结束", evidence_ids: state.judgment?.evidence_ids ?? [] } });
}

export function resumeRecoveryObservation(state: RecoveryTaskState): RecoveryTaskState {
  if (state.simulation) conflict("仿真不可等待或恢复在线效果观察");
  if (state.status !== "waiting_effect") conflict("只有等待效果的任务可以由上游继续");
  // Do not reset the deadline here: first consume new evidence and preserve the paused phase.
  return next(state, { status: "running", requiresFreshObservation: true });
}

export function terminateRecoveryTask(state: RecoveryTaskState): RecoveryTaskState {
  if (!["running", "waiting_owner", "waiting_effect"].includes(state.status)) conflict("当前状态不允许停止任务");
  return next(state, { status: "canceled", observationDeadlineAt: null, ownerDeadlineAt: null, requiresFreshObservation: false,
    ...(state.executionRetry ? { executionRetry: { ...state.executionRetry, retryAt: 0 } } : {}) });
}

export function recoveryTaskResult(state: RecoveryTaskState & Pick<RecoveryStoredTask, "interventionStatus">, terminationReason?: string): RecoveryTaskResult {
  if (state.status !== "waiting_effect" && state.status !== "completed" && state.status !== "failed" && state.status !== "canceled") {
    conflict("只有效果等待超时或任务结束才回传上游");
  }
  return { event_id: state.request.event_id, task_id: state.taskId, result_version: state.version,
    status: state.status, phase: state.phase,
    waiting_reason: state.status === "waiting_effect" ? "effect" : null,
    ...(state.completionReason ? { completion_reason: state.completionReason } : {}),
    effect: state.judgment?.effect ?? "unknown", intervention_status: state.level === "debug" ? "none" : state.interventionStatus ?? "unknown",
    result_text: state.status === "canceled" ? terminationReason ?? "用户停止本次自愈任务" : state.judgment?.summary ?? "",
    evidence_ids: state.judgment?.evidence_ids ?? [], evidence: state.evidence.filter(item => state.judgment?.evidence_ids.includes(item.evidence_id)),
    ...(state.delivery ? { delivery: state.delivery.delivery } : {}) };
}
