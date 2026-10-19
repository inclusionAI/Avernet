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

/**
 * Log an internal API call with structured data.
 * Outputs to both console and a dedicated log file.
 */
export function apiLog(method: ApiLogMethod, path: string, details: Record<string, unknown>): void {
  const timestamp = new Date().toISOString();
  const entry = {
    ts: timestamp,
    method,
    path,
    ...details,
  };
  const line = JSON.stringify(entry);

  // Console output (always)
  console.log(`[internal-api] ${method} ${path} ${details.status ?? "pending"}`);

  // File output (best-effort)
  try {
    ensureLogDir();
    appendFileSync(LOG_FILE, line + "\n", "utf-8");
    writeFailureReported = false;
  } catch (error) {
    // Keep serving requests, but make an unavailable log destination visible.
    if (!writeFailureReported) {
      writeFailureReported = true;
      console.error(`[clawweb] Cannot write internal API log to ${LOG_FILE}`, error);
    }
  }
}

/**
 * Log a request body (truncated for large payloads).
 */
export function apiLogBody(method: ApiLogMethod, path: string, body: unknown, extra: Record<string, unknown> = {}): void {
  const bodyStr = typeof body === "string" ? body : JSON.stringify(body);
  const truncated = bodyStr.length > 2000 ? bodyStr.substring(0, 2000) + "...[truncated]" : bodyStr;
  apiLog(method, path, { bodyPreview: truncated, ...extra });
}