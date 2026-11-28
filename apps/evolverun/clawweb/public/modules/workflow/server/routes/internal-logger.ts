import { apiErrorDiagnostic, type ApiDiagnosticResponse } from "./error-log-format.js";
import { formatHttpLog, httpLogLevel } from "@avernet/clawweb-shared/server/services/file-log-format";
/**
 * Internal API Logger — structured logging for all ClawMind↔clawweb API calls.
 * Rate-limits console summaries; every call is queued best-effort for the fixed log.
 */
import { createBoundedFileLogSink } from "@avernet/clawweb-shared/server/logging/bounded-file-log-sink";
import { boundedLogValue } from "@avernet/clawweb-shared/server/logging/bounded-log-value";
import { redactLogText } from "@avernet/clawweb-shared/server/logging/redact-log-text";
import { join } from "node:path";
import { homedir } from "node:os";

// Fixed log files are rotated and retained by the deployment's logrotate rules.
// The production process runs as admin, whose home is /home/admin.
const LOG_DIR = join(homedir(), "logs", "clawweb");
const LOG_FILE = join(LOG_DIR, "clawweb-internal-api.log");

const sink = createBoundedFileLogSink({ filePath: LOG_FILE });
export function flushInternalApiLog(): Promise<void> { return sink.flush(); }

// Fixed severity buckets: no per-path/status map and no per-request stdout I/O.
const summaries = {
  INFO: { last: -Infinity, suppressed: 0 },
  WARN: { last: -Infinity, suppressed: 0 },
  ERROR: { last: -Infinity, suppressed: 0 },
};

type ApiLogMethod = "READ" | "WRITE" | "PUT" | "DELETE";

export const RUN_COMPLETION_LOG_PATH = "/runs/:flowId/completion";
export const NODE_RECONCILE_LOG_PATH = "/node-executions/:flowId/reconcile-stale-running";

// Match the unsampled path: the distinguishing suffix may follow a long ID.
function closeoutPath(method: ApiLogMethod, path: string): string | undefined {
  if (method !== "PUT") return;
  if (/^(?:\/api\/internal)?\/runs\/[^/?]+\/completion\/?(?:\?|$)/i.test(path)) return RUN_COMPLETION_LOG_PATH;
  if (/^(?:\/api\/internal)?\/node-executions\/[^/?]+\/reconcile-stale-running\/?(?:\?|$)/i.test(path)) return NODE_RECONCILE_LOG_PATH;
}

function closeoutDetails(details: Record<string, unknown>): Record<string, unknown> {
  // A numeric HTTP status is the entire allowlist. Do not enumerate or invoke getters.
  const status: unknown = Object.getOwnPropertyDescriptor(details, "httpStatus")?.value
    ?? Object.getOwnPropertyDescriptor(details, "status")?.value;
  return typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599 ? { status } : {};
}

// The host's diagnostic handoff has a stack slot. Use a literal observation,
// not a captured stack or anything derived from the exception/response.
const closeoutFailure = Object.freeze({ message: "Workflow closeout failed",
  stack: "Workflow closeout failure observed (exception withheld)", stackSource: "observation" as const });

// Explicit workflow/runtime families only; unrelated reads retain the shared policy.
const taskGuardInternalPaths = [
  "/runs", "/flow-control", "/langfuse-ingestion", "/approval-cards", "/task-guard",
  "/workflow-repair-publication", "/node-executions", "/events", "/metrics", "/alerts",
  "/scheduled-triggers", "/webhook-triggers", "/webhook-events", "/facades", "/bot-workflow-permissions",
  "/node-step-traces", "/hallucination-checks", "/deploy-history", "/execution-step-logs",
  "/http-callback-logs", "/run-logs", "/retry-requests", "/flow-run-links", "/run-reads",
  "/reads/workflow-specs", "/reads/notification-configs", "/reads/facade-bindings",
  "/reads/http-callback-configs", "/reads/http-callback-logs",
];
const taskGuardInternalEndpoints = ["/validate-bot", "/reads/run-requests", "/run-requests/failures"];
function isTaskGuardPath(path: string): boolean {
  const offset = path.startsWith("/api/internal/") ? "/api/internal".length : 0;
  const atEnd = (end: number) => end === path.length || path[end] === "?";
  return taskGuardInternalPaths.some(prefix => path.startsWith(prefix, offset)
    && (atEnd(offset + prefix.length) || path[offset + prefix.length] === "/"))
    || taskGuardInternalEndpoints.some(endpoint => {
      const end = offset + endpoint.length;
      return path.startsWith(endpoint, offset) && (atEnd(end) || (path[end] === "/" && atEnd(end + 1)));
    });
}

/**
 * Log an internal API call with structured data.
 * Outputs to both console and a dedicated log file.
 */
export function apiLog(method: ApiLogMethod, path: string, details: Record<string, unknown>, exception?: unknown, response?: ApiDiagnosticResponse): void {
  try {
    const now = new Date();
    const closeout = closeoutPath(method, path);
    const safePath = closeout ?? redactLogText(path, 1024);
    const safeDetails = closeout ? closeoutDetails(details) : boundedLogValue(details, { maxChars: 8192 }) as Record<string, unknown>;
    const status = safeDetails.httpStatus ?? safeDetails.status;
    // A fail-closed redaction can hide the whole path, not its severity policy.
    const taskGuard = isTaskGuardPath(path) || (path !== safePath && isTaskGuardPath(safePath));
    const level = taskGuard && typeof status === "number" && status >= 400 && status < 500
      ? "WARN" : httpLogLevel(status);
    const originalDiagnostic = closeout || !taskGuard ? undefined : exception !== undefined ? apiErrorDiagnostic(exception)
      : level === "ERROR" ? apiErrorDiagnostic(typeof safeDetails.error === "string" ? safeDetails.error : `${method} ${safePath} ${typeof status === "number" ? status : "failure"}`)
      : undefined;
    const diagnostic = closeout && level === "ERROR" ? closeoutFailure : originalDiagnostic ? {
      message: redactLogText(originalDiagnostic.message, 2048),
      stack: redactLogText(originalDiagnostic.stack, 8192),
      stackSource: originalDiagnostic.stackSource,
    } : undefined;
    if (response && level === "ERROR" && diagnostic) {
      response.locals ??= {};
      response.locals.taskGuardApiDiagnostic = diagnostic;
    }
    const stack = diagnostic?.stack;
    const entry = {
      ts: now.toISOString(),
      method,
      path: safePath,
      ...safeDetails,
      ...(stack ? { stack, stackSource: diagnostic!.stackSource } : {}),
    };
    const line = formatHttpLog(now, level, "internal-api", entry);

    // Structured entries are retained best-effort even when stdout is broken or
    // the summary is suppressed. The sink owns bounded queue/drop accounting.
    sink.write(line + "\n");
    const bucket = summaries[level];
    const timestamp = now.getTime();
    if (timestamp - bucket.last < 1000) {
      bucket.suppressed = Math.min(bucket.suppressed + 1, Number.MAX_SAFE_INTEGER);
      return;
    }
    const suppressed = bucket.suppressed;
    bucket.last = timestamp;
    bucket.suppressed = 0;
    const consoleMethod = level === "ERROR" ? "error" : level === "WARN" ? "warn" : "log";
    const label = status == null ? "pending" : typeof status === "string" ? redactLogText(status, 256)
      : typeof status === "number" || typeof status === "boolean" ? String(status) : "[status]";
    const summary = `[internal-api] ${method} ${safePath} ${label}` + (suppressed ? ` suppressed=${suppressed}` : "");
    if (stack) console[consoleMethod]("%s %s", summary, stack);
    else console[consoleMethod]("%s", summary);
  } catch {
    // Logging must not break serving requests, including hostile values/stdout.
  }
}

/**
 * Log a request body (truncated for large payloads).
 */
export function apiLogBody(method: ApiLogMethod, path: string, body: unknown, extra: Record<string, unknown> = {}): void {
  const closeout = closeoutPath(method, path);
  if (closeout) return apiLog(method, closeout, extra);
  const preview = boundedLogValue(body, { maxChars: 2000 });
  const safeExtra = boundedLogValue(extra, { maxChars: 4096 }) as Record<string, unknown>;
  apiLog(method, path, { bodyPreview: typeof preview === "string" ? preview : JSON.stringify(preview), ...safeExtra });
}
