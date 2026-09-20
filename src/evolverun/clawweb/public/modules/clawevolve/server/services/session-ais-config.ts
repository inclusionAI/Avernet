import type { SessionAisOptions } from "../contracts/ais-executor.js";
/** Deployment selection is frozen in each task; existing tasks retain their Snapshot. */
export type SessionAisBaseConfig = { snapshotId: number; packageId: string; deadlineAt: number };

export function sessionAisDeadline(seconds: number, now = Date.now()): number {
  if (!Number.isSafeInteger(seconds) || seconds < 60) throw new Error("Invalid session AIS deadline configuration");
  return now + seconds * 1000;
}

export function sessionAisBaseConfig(options: SessionAisOptions, now = Date.now()): SessionAisBaseConfig | undefined {
  if (!options.deployment) return undefined;
  return { ...options.deployment, deadlineAt: sessionAisDeadline(options.deadlineSeconds, now) };
}

export function sessionAisParams(config: {
  taskId: string; stepId: string; attempt: number; clawwebUrl: string;
  aisBase: SessionAisBaseConfig;
}, taskType: string, input: Record<string, unknown>): Record<string, string> {
  return { "${clawevolve_params}": JSON.stringify({
    taskType, taskId: config.taskId, stepId: config.stepId, attempt: config.attempt, input,
    runtime: { clawwebUrl: config.clawwebUrl, package: { packageId: config.aisBase.packageId } },
  }) };
}
