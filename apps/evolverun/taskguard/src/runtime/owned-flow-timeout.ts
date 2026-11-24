/** Frozen, process-owned watchdog policy. All times are epoch milliseconds. */
export type FrozenFlowTimeoutPolicy = Readonly<{
  startedAt: number;
  timeoutMs: number;
  deadlineAt: number | null;
}>;

/** Called only when creating a new flow, never when recovering an old run. */
export function freezeFlowTimeoutPolicy(
  startedAt: number,
  workflowTimeoutMinutes: number | undefined,
  globalTimeoutMinutes: number,
): FrozenFlowTimeoutPolicy {
  const timeoutMs = (workflowTimeoutMinutes ?? globalTimeoutMinutes) * 60_000;
  const policy = readFrozenFlowTimeoutPolicy({
    startedAt,
    timeoutMs,
    deadlineAt: timeoutMs === 0 ? null : startedAt + timeoutMs,
  });
  if (!policy) throw new Error("Invalid flow timeout policy");
  return policy;
}

/** Do not guess a timeout for legacy/recovered state without a valid policy. */
export function readFrozenFlowTimeoutPolicy(value: unknown): FrozenFlowTimeoutPolicy | undefined {
  if (!value || typeof value !== "object") return undefined;
  const { startedAt, timeoutMs, deadlineAt } = value as Record<string, unknown>;
  if (typeof startedAt !== "number" || !Number.isFinite(startedAt) || startedAt <= 0 ||
      typeof timeoutMs !== "number" || !Number.isFinite(timeoutMs) || timeoutMs < 0) {
    return undefined;
  }
  if (timeoutMs === 0 ? deadlineAt !== null :
      typeof deadlineAt !== "number" || !Number.isFinite(deadlineAt) || deadlineAt !== startedAt + timeoutMs) {
    return undefined;
  }
  return Object.freeze({ startedAt, timeoutMs, deadlineAt: deadlineAt as number | null });
}
