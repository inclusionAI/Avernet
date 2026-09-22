import { RepairError } from "./errors.js";

export const REPAIR_AIS_CREDENTIALS_KEY = "${clawevolve_credentials}";
export const REPAIR_AIS_PACKAGE = "clawevolve-repair";
export const REPAIR_RUNTIME_ARTIFACTS = ["artifactBundle", "runtimeBundle", "openclawSessions"] as const;

export function repairRuntimeArtifact(taskId: string, stepId: string, name: string) {
  if (!(REPAIR_RUNTIME_ARTIFACTS as readonly string[]).includes(name)) {
    throw new RepairError(422, "invalid_repair_artifact_name", "未知 AIS 运行归档");
  }
  return { objectKey: "evolution/" + taskId + "/repair/" + stepId + "/ais/" + name + ".tar.gz", contentType: "application/gzip" };
}

/** Private platform parameter is never frozen as task input. */
export function repairAisParams(legacy: Record<string, any>, heartbeatSeconds: number): Record<string, string> {
  const input = structuredClone(legacy.input);
  const modelApiKey = input.agent?.openclaw?.modelApiKey;
  if (input.agent?.openclaw) delete input.agent.openclaw.modelApiKey;
  const credentials = {
    bearerToken: legacy.runtime.executionTicket,
    ...(modelApiKey ? { modelApiKey } : {}),
  };
  if (typeof credentials.bearerToken !== "string" || !credentials.bearerToken) {
    throw new Error("Missing Repair execution credential");
  }
  return {
    "${clawevolve_params}": JSON.stringify({
      taskType: "repair", taskId: legacy.taskId, stepId: legacy.stepId, attempt: legacy.attempt,
      input: { ...input, execution: legacy.execution, executionTimings: legacy.runtime.timings },
      runtime: {
        clawwebUrl: legacy.runtime.clawwebUrl,
        package: { packageId: REPAIR_AIS_PACKAGE },
        callback: { path: new URL(legacy.runtime.toolsBaseUrl).pathname + "/ais", heartbeatSeconds },
      },
    }),
    [REPAIR_AIS_CREDENTIALS_KEY]: JSON.stringify(credentials),
  };
}

export function validateRuntimeArtifacts(taskId: string, stepId: string, value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new RepairError(422, "invalid_repair_runtime_artifacts", "AIS 归档元数据缺失");
  }
  const validated: Record<string, unknown> = {};
  for (const [name, raw] of Object.entries(value)) {
    const expected = repairRuntimeArtifact(taskId, stepId, name);
    const item = raw as Record<string, unknown>;
    if (!item || Array.isArray(item) || item.objectKey !== expected.objectKey || item.contentType !== expected.contentType
      || !Number.isSafeInteger(item.size) || Number(item.size) <= 0
      || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)) {
      throw new RepairError(422, "invalid_repair_runtime_artifacts", "AIS 归档元数据不合法");
    }
    validated[name] = { ...expected, size: item.size, sha256: item.sha256 };
  }
  return validated;
}
