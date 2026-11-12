import type { RecoveryEffectWait, RecoveryMaintenanceTask, RecoveryStoredTask } from "./task-contracts.js";

const HOUR_MS = 60 * 60 * 1000;
export const EFFECT_RESUME_LIMIT = 3;
export const EFFECT_RESUME_WINDOW_MS = 24 * HOUR_MS;

/** Historical waiting transitions precede the next resume and end with an observation report. */
export function recoveryEffectWait(task: RecoveryStoredTask): RecoveryEffectWait | undefined {
  if (task.effectWait) return task.effectWait;
  const firstResume = task.timeline.findIndex(event => event.kind === "resumed");
  const latest = task.timeline.findLast(event => event.kind === "observation")?.at_ms;
  const first = firstResume < 0 ? latest : task.timeline.slice(0, firstResume).findLast(event => event.kind === "observation")?.at_ms;
  if (first === undefined || latest === undefined) return undefined;
  return { firstAt: first, sinceAt: latest, resumes: 0 };
}

export function effectResumeDispatchExpired(task: RecoveryStoredTask, now: number): boolean {
  return Boolean(task.effectResumeSlot && task.effectResumeSlot.lastExecutionNo === undefined && task.effectWait
    && (task.phase === "observe" || task.phase === "verify") && now >= task.effectWait.firstAt + EFFECT_RESUME_WINDOW_MS);
}

export function effectResumeAction(task: RecoveryMaintenanceTask | RecoveryStoredTask, now: number): "resume" | "close" | undefined {
  if (task.status !== "waiting_effect" || (task.phase !== "observe" && task.phase !== "verify")) return undefined;
  const wait = task.effectWait;
  const first = wait?.firstAt ?? task.observationDeadlineAt;
  if ((wait?.resumes ?? 0) >= EFFECT_RESUME_LIMIT || (first !== null && now >= first + EFFECT_RESUME_WINDOW_MS)) return "close";
  if (task.effectResumeSlot) return undefined;
  const since = wait?.sinceAt ?? task.observationDeadlineAt;
  if (since !== null && now >= since + HOUR_MS) return "resume";
}
