import type { RecoveryEffectWait, RecoveryMaintenanceTask, RecoveryStoredTask } from "./task-contracts.js";

export const defaultEffectResumePolicy = { intervalSeconds: 3600, windowSeconds: 86400, maxResumes: 3 };
export type EffectResumePolicy = typeof defaultEffectResumePolicy;

export function parseEffectResumeConfig(input: unknown): EffectResumePolicy & { concurrency: number } {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid session recovery effect resume config");
  const { concurrency, intervalSeconds, windowSeconds, maxResumes } = { ...defaultEffectResumePolicy, ...input as EffectResumePolicy & { concurrency: number } };
  const valid = (n: number, min: number, max: number) => Number.isSafeInteger(n) && n >= min && n <= max;
  if (!valid(concurrency, 1, 10) || !valid(intervalSeconds, 1, 30 * 86400)
    || !valid(windowSeconds, 1, 30 * 86400) || !valid(maxResumes, 1, 100)) {
    throw new Error("Invalid session recovery effect resume config");
  }
  return { concurrency, intervalSeconds, windowSeconds, maxResumes };
}

/** Historical wait transitions precede the next resume and end with an observation report. */
export function recoveryEffectWait(task: Pick<RecoveryStoredTask, "effectWait" | "timeline">): RecoveryEffectWait | undefined {
  if (task.effectWait) return task.effectWait;
  const firstResume = task.timeline.findIndex(event => event.kind === "resumed");
  const isWait = (event: RecoveryStoredTask["timeline"][number]) => event.kind === "observation"
    && (!event.payload.judgment || (event.payload.judgment as { effect?: string }).effect === "unknown")
    && !event.payload.sourceToolCallId;
  const observations = task.timeline.filter(isWait);
  const latest = observations.at(-1)?.at_ms;
  const first = firstResume < 0 ? latest : task.timeline.slice(0, firstResume).findLast(isWait)?.at_ms;
  if (first === undefined || latest === undefined) return undefined;
  return { firstAt: first, sinceAt: latest, resumes: task.timeline.filter(event => event.kind === "resumed" && event.payload.automatic === true).length };
}

export function effectResumeTiming(wait: RecoveryEffectWait, now: number, policy: EffectResumePolicy) {
  const deadline = wait.firstAt + policy.windowSeconds * 1000;
  const next = wait.sinceAt + policy.intervalSeconds * 1000;
  return { next, deadline, remaining: wait.resumes < policy.maxResumes && Math.max(now, next) < deadline };
}

export function effectResumeDispatchExpired(task: RecoveryStoredTask, now: number, policy: EffectResumePolicy | null): boolean {
  return Boolean(task.effectResumeSlot && task.effectResumeSlot.lastExecutionNo === undefined && task.effectWait
    && (task.phase === "observe" || task.phase === "verify") && (!policy || now >= task.effectWait.firstAt + policy.windowSeconds * 1000
      || task.effectWait.resumes > policy.maxResumes));
}

export function effectResumeAction(task: RecoveryMaintenanceTask | RecoveryStoredTask, now: number, policy: EffectResumePolicy): "resume" | "close" | undefined {
  if (task.status !== "waiting_effect" || (task.phase !== "observe" && task.phase !== "verify")
    || ("execution" in task && task.execution?.effectRecheck && task.execution.state !== "ended")) return undefined;
  // Compact maintenance rows use the deadline as a prefilter. Full rows reconstruct legacy waits before claiming.
  const wait = task.effectWait ?? (task.observationDeadlineAt === null ? undefined
    : { firstAt: task.observationDeadlineAt, sinceAt: task.observationDeadlineAt, resumes: 0 });
  if (!wait) return undefined;
  const timing = effectResumeTiming(wait, now, policy);
  if (!timing.remaining) return "close";
  if (!task.effectResumeSlot && now >= timing.next) return "resume";
}
