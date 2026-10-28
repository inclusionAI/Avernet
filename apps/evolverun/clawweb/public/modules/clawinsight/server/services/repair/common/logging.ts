import { hostname } from "node:os";
import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import { redactText } from "@avernet/clawweb-shared/server/services/redaction";

type RepairLogFields = {
  environment?: string; targetEnvironment?: string; eventId?: string; botId?: string;
  taskId?: string; executionId?: string; jobId?: string; stepId?: string; toolCallId?: string;
  phase?: string; status?: string; outcome?: string; operation?: string; reason?: string; errorCode?: string;
  count?: number; attempt?: number; elapsedMs?: number; retryAtMs?: number; stateVersion?: number;
};
const allowed = new Set(["environment", "targetEnvironment", "eventId", "botId", "taskId", "executionId", "jobId",
  "stepId", "toolCallId", "phase", "status", "outcome", "operation", "reason", "errorCode", "count", "attempt",
  "elapsedMs", "retryAtMs", "stateVersion"]);

/** Metadata only: never serialize raw errors, credentials, tool inputs or provider payloads. */
export function logRepairEvent(scope: "session-recovery" | "agent-execution-monitor", job: string, event: string, fields: RepairLogFields = {}): void {
  try {
    const safe = Object.fromEntries(Object.entries(fields).filter(([key, value]) => allowed.has(key)
      && (typeof value === "string" || typeof value === "boolean" || typeof value === "number" && Number.isFinite(value))));
    console.info(`[${scope}] ` + JSON.stringify({ ...safe, job, event,
      environment: fields.environment ?? getCurrentEnv(), instance: hostname(), pid: process.pid, ts: new Date().toISOString() }));
  } catch { /* Observability must never change task execution or settlement. */ }
}

export function repairLogError(error: unknown): Pick<RepairLogFields, "errorCode" | "reason"> {
  const code = error && typeof error === "object" && "code" in error ? error.code : undefined;
  return {
    errorCode: typeof code === "string" && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(code) ? code
      : error instanceof Error ? error.name : "UNKNOWN_ERROR",
    reason: redactText(error instanceof Error ? error.message : String(error), 1024),
  };
}
