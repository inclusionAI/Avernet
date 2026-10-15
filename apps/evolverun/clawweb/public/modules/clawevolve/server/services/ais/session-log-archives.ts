import type { EvolveStepRow, EvolveTaskRow } from "../../repositories/evolve-repository.js";
import { aisContractForStep } from "./ais-artifact-contract.js";

function record(text: string | null): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(text ?? "null");
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  } catch { return {}; }
}

/** Expose Base's already uploaded Agent sessions through the existing log archive API. */
export function aisSessionLogArchives(task: EvolveTaskRow, steps: readonly EvolveStepRow[], bucket: string) {
  return steps.filter(step => step.task_id === task.task_id && step.step_type === "agent_execution")
    .sort((a, b) => b.id - a.id).flatMap(step => {
      const contract = aisContractForStep(record(task.config_json), record(step.ext_json), step.step_id);
      const executionId = contract?.aisBase.executionId;
      if (typeof executionId !== "string" || !/^[A-Za-z0-9_-]+$/u.test(executionId)) return [];
      const objectKey = `evolution/${task.task_id}/executions/${executionId}/openclawSessions.tar.gz`;
      const spec = contract?.artifacts.openclawSessions;
      const artifacts = record(step.output_json).artifacts;
      const artifact = artifacts && typeof artifacts === "object" && !Array.isArray(artifacts)
        ? (artifacts as Record<string, unknown>).openclawSessions : null;
      if (!spec || spec.objectKey !== objectKey || spec.contentType !== "application/gzip"
        || !artifact || typeof artifact !== "object" || Array.isArray(artifact)) return [];
      const uploaded = artifact as Record<string, unknown>;
      if (uploaded.objectKey !== objectKey || uploaded.contentType !== spec.contentType
        || !Number.isSafeInteger(uploaded.size) || Number(uploaded.size) < 1
        || typeof uploaded.sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(uploaded.sha256)) return [];
      return [{
        archiveId: `AIS-${step.id}`, taskId: task.task_id, status: "succeeded" as const,
        requestedBy: task.created_by, transport: "ais",
        artifact: { ref: `oss://${bucket}/${objectKey}`, size: Number(uploaded.size), sha256: uploaded.sha256, contentType: spec.contentType },
        metadata: { source: "ais", executionId, stepId: step.step_id }, error: null,
        startedAt: step.started_at, completedAt: step.completed_at,
        gmtCreate: step.gmt_create, gmtModified: step.gmt_modified,
      }];
    });
}
