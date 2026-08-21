import { appendFile, mkdir, readdir, rename, stat, unlink } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import type { FlowEvent } from "./types.js";

export type WorkflowJsonlRecord = {
  time: string;
  flow_id: string | null;
  event_type: string;
  message: string;
  /** Top-level node_id for easy grep/filter (also kept in details for backward compat). */
  node_id: string | null;
  /** Bot ID identifying which bot instance produced this log entry. */
  bot_id: string | null;
  /** Structured error info — preserved from Error objects instead of String(err). */
  error_name: string | null;
  error_stack: string | null;
  error_cause: string | null;
  details: Record<string, unknown>;
};

type AppendOptions = {
  baseDir?: string;
};

let warnedWriteFailure = false;

/**
 * Extract structured error info from any thrown value, preserving
 * Error.name, Error.message, Error.stack, and Error.cause instead of
 * the lossy `String(err)` which drops all of these.
 */
export function extractErrorInfo(err: unknown): {
  error_name: string | null;
  error_stack: string | null;
  error_cause: string | null;
  error_message: string;
} {
  if (err instanceof Error) {
    let error_cause: string | null = null;
    if (err.cause instanceof Error) {
      error_cause = `${err.cause.name}: ${err.cause.message}`;
    } else if (err.cause != null) {
      error_cause = String(err.cause);
    }
    return {
      error_name: err.name || null,
      error_stack: err.stack || null,
      error_cause,
      error_message: err.message,
    };
  }
  if (err != null) {
    return {
      error_name: null,
      error_stack: null,
      error_cause: null,
      error_message: String(err),
    };
  }
  return { error_name: null, error_stack: null, error_cause: null, error_message: "" };
}

export function defaultWorkflowLogDir(): string {
  // macOS → dev; Linux → production
  if (process.platform === "darwin") {
    return join(homedir(), ".openclaw", "logs", "taskguard");
  }
  return process.env.WORKFLOW_LOG_DIR || join(homedir(), ".openclaw", "logs", "taskguard");
}

/** Rolling log configuration — read from env vars with sensible defaults. */
export type RollingConfig = {
  /** Max size of the active log file before rolling (in MB). Default: 10. */
  maxFileSizeMb: number;
  /** Max number of rolled backup files to keep (clawmind.log.1 .. clawmind.log.N). Default: 5. */
  maxFileCount: number;
  /** Max age of log files in days before auto-deletion. Default: 3. */
  retentionDays: number;
};

export function loadRollingConfig(): RollingConfig {
  return {
    maxFileSizeMb: Number(process.env.CLAWMIND_LOG_MAX_FILE_SIZE_MB) || 10,
    maxFileCount: Number(process.env.CLAWMIND_LOG_MAX_FILE_COUNT) || 5,
    retentionDays: Number(process.env.CLAWMIND_LOG_RETENTION_DAYS) || 3,
  };
}

/**
 * Resolve the active log file path.
 *
 * Path: <baseDir>/clawmind.log
 *
 * If the active file exceeds maxFileSizeMb, perform rolling:
 *   clawmind.log.N   → delete (if N exceeds maxFileCount)
 *   clawmind.log.N-1 → clawmind.log.N
 *   ...
 *   clawmind.log.1   → clawmind.log.2
 *   clawmind.log     → clawmind.log.1
 * Then a fresh clawmind.log is created on the next appendFile.
 */
export async function resolveLogFilePath(
  baseDir: string,
  config: RollingConfig,
): Promise<string> {
  const logPath = join(baseDir, "clawmind.log");
  const maxBytes = config.maxFileSizeMb * 1024 * 1024;

  try {
    const fileSize = (await stat(logPath)).size;
    if (fileSize < maxBytes) {
      return logPath;
    }
  } catch {
    // File doesn't exist yet — no rolling needed
    return logPath;
  }

  // Active file exceeded size limit — perform rolling
  try {
    // Delete the oldest backup if it exceeds maxFileCount
    const oldestBackup = join(baseDir, `clawmind.log.${config.maxFileCount}`);
    try {
      await unlink(oldestBackup);
    } catch {
      // Oldest backup may not exist — that's fine
    }

    // Shift backups: .N-1 → .N, .N-2 → .N-1, ..., .1 → .2
    for (let seq = config.maxFileCount - 1; seq >= 1; seq--) {
      const src = join(baseDir, `clawmind.log.${seq}`);
      const dst = join(baseDir, `clawmind.log.${seq + 1}`);
      try {
        await rename(src, dst);
      } catch {
        // Source may not exist — skip
      }
    }

    // Move active log to .1
    await rename(logPath, join(baseDir, "clawmind.log.1"));
  } catch (rollErr) {
    // Rolling failed — continue writing to the current (oversized) file
    // rather than losing logs. Next write will retry rolling.
    console.warn("[taskguard] log rolling failed, continuing with current file:", rollErr instanceof Error ? rollErr.message : String(rollErr));
  }

  return logPath;
}

/**
 * Clean up old log files based on retention days.
 * Called probabilistically (1% chance per write) to avoid overhead.
 */
export async function cleanupOldLogFiles(
  baseDir: string,
  config: RollingConfig,
): Promise<void> {
  const cutoffMs = Date.now() - config.retentionDays * 24 * 60 * 60 * 1000;
  try {
    const entries = await readdir(baseDir);
    for (const entry of entries) {
      // Match clawmind.log.N (rolled backups) — never delete the active clawmind.log
      if (!entry.startsWith("clawmind.log.") ) continue;
      try {
        const filePath = join(baseDir, entry);
        const fileStat = await stat(filePath);
        if (fileStat.mtimeMs < cutoffMs) {
          await unlink(filePath);
        }
      } catch {
        // Skip files that disappear or are inaccessible
      }
    }
  } catch {
    // Directory doesn't exist or is inaccessible — nothing to clean
  }
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function pad3(value: number): string {
  return String(value).padStart(3, "0");
}

function formatOffset(minutesWestOfUtc: number): string {
  const minutesEastOfUtc = -minutesWestOfUtc;
  const sign = minutesEastOfUtc >= 0 ? "+" : "-";
  const abs = Math.abs(minutesEastOfUtc);
  return `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`;
}

export function formatLocalIsoWithOffset(date: Date): string {
  return [
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`,
    "T",
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`,
    ".",
    pad3(date.getMilliseconds()),
    formatOffset(date.getTimezoneOffset()),
  ].join("");
}

export async function appendWorkflowJsonlLog(
  record: WorkflowJsonlRecord,
  options: AppendOptions = {},
): Promise<void> {
  try {
    const baseDir = options.baseDir ?? defaultWorkflowLogDir();
    await mkdir(baseDir, { recursive: true });
    const config = loadRollingConfig();
    const logPath = await resolveLogFilePath(baseDir, config);
    await appendFile(logPath, `${JSON.stringify(record)}\n`, "utf8");

    // Probabilistic cleanup: 1% chance to run on each write
    if (Math.random() < 0.01) {
      void cleanupOldLogFiles(baseDir, config).catch(() => {});
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!warnedWriteFailure) {
      warnedWriteFailure = true;
      console.warn("[taskguard] log write failed", message);
    }
  }
}

/**
 * Build a WorkflowJsonlRecord directly (without a FlowEvent) for ad-hoc log entries.
 * Auto-fills time, node_id, bot_id, and structured error from rawError.
 */
export function buildDirectLogRecord(params: {
  flowId: string | null;
  eventType: string;
  message: string;
  nodeId?: string | null;
  botId?: string | null;
  sessionKey?: string | null;
  details?: Record<string, unknown>;
  rawError?: unknown;
}): WorkflowJsonlRecord {
  const errorInfo = params.rawError !== undefined
    ? extractErrorInfo(params.rawError)
    : { error_name: null as string | null, error_stack: null as string | null, error_cause: null as string | null, error_message: "" };
  const details: Record<string, unknown> = {
    session_key: params.sessionKey ?? null,
    ...params.details,
    ...(errorInfo.error_message ? { error: errorInfo.error_message } : {}),
  };
  return {
    time: formatLocalIsoWithOffset(new Date()),
    flow_id: params.flowId,
    event_type: params.eventType,
    message: params.message,
    node_id: params.nodeId ?? null,
    bot_id: params.botId ?? null,
    error_name: errorInfo.error_name,
    error_stack: errorInfo.error_stack,
    error_cause: errorInfo.error_cause,
    details,
  };
}

function eventMessage(event: FlowEvent): string {
  const node = event.nodeId ?? "节点";
  const action = event.actionId ?? "action";
  switch (event.type) {
    case "workflow_started":
      return "流程启动";
    case "workflow_preflight":
      return "流程前置校验";
    case "workflow_reopened":
      return "流程重新开启";
    case "workflow_blocked":
      return "流程阻塞";
    case "workflow_finished":
      return "流程完成";
    case "node_ready":
      return `${node} 就绪`;
    case "node_started":
      return `${node} 开始`;
    case "node_waiting":
      return `${node} 等待`;
    case "node_succeeded":
      return `${node} 成功`;
    case "node_failed":
      return `${node} 失败`;
    case "node_skipped":
      return `${node} 跳过`;
    case "action_started":
      return `${action} 开始`;
    case "action_succeeded":
      return `${action} 成功`;
    case "action_failed":
      return `${action} 失败`;
    case "chat_inject_failed":
      return "消息注入失败";
    case "embedded_agent_event":
      return `${node} Agent 事件`;
    case "collaboration_result_received":
      return "BCS 协作回调通过";
    case "collaboration_result_rejected":
      return "BCS 协作回调拒绝";
    case "subworkflow_started":
      return `${node} 子流程启动`;
    case "subworkflow_finished":
      return `${node} 子流程完成`;
    default:
      return event.type;
  }
}

export function buildWorkflowLogRecord(params: {
  event: FlowEvent;
  sessionKey?: string | null;
  botId?: string | null;
  ownerId?: string | null;
  /** Raw error object — if provided, overrides event.error string with structured extraction. */
  rawError?: unknown;
}): WorkflowJsonlRecord {
  const eventDate = new Date(params.event.time);
  const time = formatLocalIsoWithOffset(Number.isNaN(eventDate.getTime()) ? new Date() : eventDate);

  // Use rawError for structured extraction if available, otherwise fall back to event.error string
  const errorInfo = params.rawError !== undefined
    ? extractErrorInfo(params.rawError)
    : (params.event.error
        ? { error_name: null, error_stack: null, error_cause: null, error_message: params.event.error }
        : { error_name: null, error_stack: null, error_cause: null, error_message: "" });

  const details: Record<string, unknown> = {
    workflow_id: params.event.workflowId,
    node_id: params.event.nodeId ?? null,
    action_id: params.event.actionId ?? null,
    attempt: params.event.attempt ?? null,
    session_key: params.sessionKey ?? null,
    source: "clawmind",
    error: errorInfo.error_message || null,
  };
  if (params.event.data && Object.keys(params.event.data).length > 0) {
    details.data = params.event.data;
  }

  return {
    time,
    flow_id: params.event.flowId ?? null,
    event_type: params.event.type,
    message: eventMessage(params.event),
    node_id: params.event.nodeId ?? null,
    bot_id: params.botId ?? null,
    error_name: errorInfo.error_name,
    error_stack: errorInfo.error_stack,
    error_cause: errorInfo.error_cause,
    details,
  };
}
