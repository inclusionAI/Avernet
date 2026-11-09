import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { SessionRecoveryError } from "./errors.js";
import { validateRecoveryEvidenceReferences } from "./task-state.js";
import { RECOVERY_OWNER_REQUEST_REASONS, type RecoveryOwnerRequestInput, type RecoveryStoredTask, type RecoveryTaskState } from "./task-contracts.js";

function fail(status: number, message: string): never {
  throw new SessionRecoveryError(status, "recovery_owner_request_conflict", message);
}

/** Only an active or possibly delivered attempt reserves this feedback's message. */
export function ownerContinuationState(requestId: string, attempt: { request?: unknown; result?: unknown; status?: unknown }) {
  const request = attempt.request as { owner_request_id?: unknown } | undefined;
  const receipt = attempt.result as { owner_request_id?: unknown; status?: unknown } | undefined;
  if ((request?.owner_request_id ?? receipt?.owner_request_id) !== requestId) return;
  if (attempt.status === "pending" || attempt.status === "executing" || attempt.status === "unknown") return attempt.status;
  if (attempt.status === "succeeded" && receipt?.status === "accepted") return "accepted";
}

export function hasAssessmentDelivery(task: Pick<RecoveryStoredTask, "ownerRequest" | "delivery" | "timeline">): boolean {
  const ownerRequest = task.ownerRequest;
  if (ownerRequest?.status !== "confirmed") return task.delivery?.delivery.status === "accepted" || task.delivery?.delivery.status === "unknown";
  return task.timeline.some(event => {
    if (event.kind !== "operation") return false;
    const state = ownerContinuationState(ownerRequest.requestId, event.payload);
    return state === "accepted" || state === "unknown";
  });
}

/** Owner feedback resumes assessment independently of environment-plan approval. */
export function requestOwnerCooperation(state: RecoveryTaskState, input: RecoveryOwnerRequestInput, requestId: string, now: number): RecoveryTaskState {
  if (state.simulation || state.level === "debug" || state.status !== "running" || state.phase === "apply") fail(409, "当前不能等待 Owner 处理事项");
  if (state.ownerRequest?.status === "confirmed" && state.requiresFreshObservation) fail(409, "先核验 Owner 反馈后的最新原会话，再确认剩余缺项");
  if (!input || !RECOVERY_OWNER_REQUEST_REASONS.includes(input.reason)
    || [input.summary, input.instructions].some(value => typeof value !== "string" || !value.trim() || value.length > 4000)
    || containsSecret(JSON.stringify(input)) || !Array.isArray(input.evidence_ids) || !input.evidence_ids.length) fail(422, "Owner 反馈仅支持权限处理或可核验的配置补齐，需要具体处理事项和现场证据；其他事项请普通转交并省略 owner_request");
  validateRecoveryEvidenceReferences(state, input.evidence_ids);
  if (!state.evidence.some(item => input.evidence_ids.includes(item.evidence_id)
    && ["session_read", "trajectory_read", "runtime_inspect"].includes(item.source))) fail(422, "Owner 处理事项必须引用受控现场读取");
  return { ...state, version: state.version + 1, status: "waiting_owner", observationDeadlineAt: null, ownerDeadlineAt: null,
    requiresFreshObservation: false, final_summary: undefined,
    ownerRequest: { ...input, requestId, revision: (state.ownerRequest?.revision ?? 0) + 1, status: "pending",
      resumePhase: state.phase === "assess" && state.ownerRequest?.status === "confirmed" ? state.ownerRequest.resumePhase : state.phase, createdAtMs: now } };
}

/** Caller authenticates and authorizes the actor/environment before invoking this transition. */
export function confirmOwnerRequest(state: RecoveryTaskState, requestId: string, userId: string, feedback: string, now: number): RecoveryTaskState {
  const request = state.ownerRequest;
  if (!request || request.requestId !== requestId || !["waiting_owner", "running", "waiting_effect"].includes(state.status)) fail(409, "处理事项已结束或更新，请查看当前任务");
  // Gateway retries acknowledge the original durable receipt; never dispatch another execution.
  if (request.status === "confirmed") return state;
  if (state.status !== "waiting_owner") fail(409, "任务当前未等待此处理事项");
  if (typeof feedback !== "string" || feedback.length > 4000 || containsSecret(feedback)) fail(422, "反馈格式无效");
  return { ...state, version: state.version + 1, status: "running", phase: "assess", requiresFreshObservation: true,
    observationDeadlineAt: null, ownerDeadlineAt: null, final_summary: undefined,
    ownerRequest: { ...request, status: "confirmed", feedback: { userId, text: feedback, atMs: now } } };
}
