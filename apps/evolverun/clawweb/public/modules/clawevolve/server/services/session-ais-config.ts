import type { AisArtifactSpec } from "./ais/ais-artifact-contract.js";
import type { SessionAisOptions } from "../contracts/ais-executor.js";
/** Deployment selection is frozen in each task; existing AIS Base tasks retain their Snapshot. */
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

/** Export evidence and Base archives share the existing attempt-scoped object prefix. */
export function sessionExportAisArtifacts(
  artifacts: Record<string, AisArtifactSpec>,
  single: boolean,
): Record<string, AisArtifactSpec> {
  const prefix = artifacts.result.objectKey.slice(0, artifacts.result.objectKey.lastIndexOf("/"));
  const names: Record<string, [string, string]> = {
    raw: [artifacts.raw.objectKey, single ? "application/x-ndjson" : "application/gzip"],
    manifest: [artifacts.manifest.objectKey, "application/json"],
    result: [artifacts.result.objectKey, "application/json"],
    artifactBundle: [`${prefix}/task-artifacts.tar.gz`, "application/gzip"],
    runtimeBundle: [`${prefix}/clawevolve-results.tar.gz`, "application/gzip"],
    openclawSessions: [`${prefix}/openclaw-sessions.tar.gz`, "application/gzip"],
  };
  if (single) {
    names.trajectory = [`${prefix}/session.trajectory.jsonl`, "application/x-ndjson"];
    names.trajectoryPath = [`${prefix}/session.trajectory-path.json`, "application/json"];
  }
  return Object.fromEntries(Object.entries(names).map(([name, [objectKey, contentType]]) => [name, {
    objectKey, contentType, requiredOnSuccess: !["trajectory", "trajectoryPath"].includes(name),
    allowEmpty: ["trajectory", "trajectoryPath"].includes(name),
  }]));
}
