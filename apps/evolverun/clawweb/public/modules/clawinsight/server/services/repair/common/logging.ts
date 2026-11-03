import { appendFileSync, mkdirSync } from "node:fs";
import { homedir, hostname } from "node:os";
import { join } from "node:path";
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

// Fixed log file; retention and rotation belong to the deployment's logrotate.
const LOG_DIR = join(homedir(), "logs", "clawweb");
const LOG_FILE = join(LOG_DIR, "clawweb-session-recovery.log");
let logDirReady = false;
let writeFailureReported = false;

function localTimestamp(now: Date): string {
  const pad = (value: number, width = 2) => String(value).padStart(width, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(now.getMilliseconds(), 3)}`;
}

function writeRecoveryLog(line: string): void {
  try {
    if (!logDirReady) { mkdirSync(LOG_DIR, { recursive: true }); logDirReady = true; }
    // Open the fixed path for each write so rotation never leaves a stale descriptor.
    appendFileSync(LOG_FILE, line + "\n", "utf-8");
    writeFailureReported = false;
  } catch (error) {
    if (!writeFailureReported) {
      writeFailureReported = true;
      console.error(`[clawweb] Cannot write session recovery log to ${LOG_FILE}`, error);
    }
  }
}

/** Metadata only: never serialize raw errors, credentials, tool inputs or provider payloads. */
export function logRepairEvent(scope: "session-recovery" | "agent-execution-monitor", job: string, event: string, fields: RepairLogFields = {}): void {
  try {
    const safe = Object.fromEntries(Object.entries(fields).filter(([key, value]) => allowed.has(key)
      && (typeof value === "string" || typeof value === "boolean" || typeof value === "number" && Number.isFinite(value))));
    const now = new Date();
    const line = `[${scope}] ` + JSON.stringify({ ...safe, job, event,
      environment: fields.environment ?? getCurrentEnv(), instance: hostname(), pid: process.pid, ts: now.toISOString() });
    if (scope === "session-recovery") writeRecoveryLog(`${localTimestamp(now)} ${line}`);
    else console.info(line);
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
