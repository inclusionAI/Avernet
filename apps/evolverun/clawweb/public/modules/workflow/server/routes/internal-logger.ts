import { apiErrorDiagnostic, errorStack, type ApiDiagnosticResponse } from "./error-log-format.js";
import { formatHttpLog, httpLogLevel } from "@avernet/clawweb-shared/server/services/file-log-format";
/**
 * Internal API Logger — structured logging for all ClawMind↔clawweb API calls.
 * Writes to both console (stdout) and a log file for production debugging.
 */
import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

// Fixed log files are rotated and retained by the deployment's logrotate rules.
// The production process runs as admin, whose home is /home/admin.
const LOG_DIR = join(homedir(), "logs", "clawweb");
const LOG_FILE = join(LOG_DIR, "clawweb-internal-api.log");

let logDirReady = false;
let writeFailureReported = false;

function ensureLogDir(): void {
  if (logDirReady) return;
  mkdirSync(LOG_DIR, { recursive: true });
  logDirReady = true;
}

type ApiLogMethod = "READ" | "WRITE" | "PUT" | "DELETE";

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
  const pathname = path.split("?")[0];
  const internalPath = pathname.startsWith("/api/internal/") ? pathname.slice("/api/internal".length) : pathname;
  return taskGuardInternalPaths.some(prefix => internalPath === prefix || internalPath.startsWith(prefix + "/"))
    || taskGuardInternalEndpoints.some(endpoint => internalPath === endpoint || internalPath === endpoint + "/");
}

/**
 * Log an internal API call with structured data.
 * Outputs to both console and a dedicated log file.
 */
export function apiLog(method: ApiLogMethod, path: string, details: Record<string, unknown>, exception?: unknown, response?: ApiDiagnosticResponse): void {
  try {
    const now = new Date();
    const status = details.httpStatus ?? details.status;
    const taskGuard = isTaskGuardPath(path);
    const level = taskGuard && typeof status === "number" && status >= 400 && status < 500
      ? "WARN" : httpLogLevel(status);
    const diagnostic = !taskGuard ? undefined : exception !== undefined ? apiErrorDiagnostic(exception)
      : level === "ERROR" ? apiErrorDiagnostic(typeof details.error === "string" ? details.error : `${method} ${path} ${status}`)
      : undefined;
    if (response && level === "ERROR" && diagnostic) {
      response.locals ??= {};
      response.locals.taskGuardApiDiagnostic = diagnostic;
    }
    const stack = diagnostic?.stack;
    const entry = {
      ts: now.toISOString(),
      method,
      path,
      ...details,
      ...(stack ? { stack, stackSource: diagnostic!.stackSource } : {}),
    };
    const line = formatHttpLog(now, level, "internal-api", entry);

    // Console output (always)
    const consoleMethod = level === "ERROR" ? "error" : level === "WARN" ? "warn" : "log";
    const summary = `[internal-api] ${method} ${path} ${status ?? "pending"}`;
    if (stack) console[consoleMethod](summary, stack);
    else console[consoleMethod](summary);
    ensureLogDir();
    appendFileSync(LOG_FILE, line + "\n", "utf-8");
    writeFailureReported = false;
  } catch (error) {
    // Keep serving requests, but make an unavailable log destination visible.
    if (!writeFailureReported) {
      writeFailureReported = true;
      try {
        console.error(`[clawweb] Cannot write internal API log to ${LOG_FILE}`, errorStack(error));
      } catch {
        // Both destinations can be unavailable during the same request.
      }
    }
  }
}

/**
 * Log a request body (truncated for large payloads).
 */
export function apiLogBody(method: ApiLogMethod, path: string, body: unknown, extra: Record<string, unknown> = {}): void {
  let bodyStr: string;
  try {
    bodyStr = typeof body === "string" ? body : JSON.stringify(body) ?? "";
  } catch {
    bodyStr = "[unserializable body]";
  }
  const truncated = bodyStr.length > 2000 ? bodyStr.substring(0, 2000) + "...[truncated]" : bodyStr;
  apiLog(method, path, { bodyPreview: truncated, ...extra });
}