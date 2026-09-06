/**
 * Internal API Logger — structured logging for all ClawMind↔clawweb API calls.
 * Writes to both console (stdout) and a log file for production debugging.
 */
import { appendFileSync, mkdirSync, existsSync, statSync, renameSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const LOG_DIR = join(homedir(), ".openclaw", "logs");
const LOG_FILE = join(LOG_DIR, "clawweb-internal-api.log");
const MAX_LOG_SIZE = 50 * 1024 * 1024; // 50MB rotation threshold

let logDirReady = false;

function ensureLogDir(): void {
  if (logDirReady) return;
  try {
    if (!existsSync(LOG_DIR)) {
      mkdirSync(LOG_DIR, { recursive: true });
    }
    logDirReady = true;
  } catch {
    // If we can't create the log dir, fall back to console-only
  }
}

function rotateIfNeeded(): void {
  try {
    if (!existsSync(LOG_FILE)) return;
    const stat = statSync(LOG_FILE);
    if (stat.size > MAX_LOG_SIZE) {
      const backup = LOG_FILE.replace(".log", `.${Date.now()}.log`);
      renameSync(LOG_FILE, backup);
    }
  } catch {
    // Rotation is best-effort
  }
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
    rotateIfNeeded();
    appendFileSync(LOG_FILE, line + "\n", "utf-8");
  } catch {
    // File logging is best-effort, never block the request
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