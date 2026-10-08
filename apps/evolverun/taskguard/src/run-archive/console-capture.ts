/**
 * @deprecated Replaced by RunLogUploader (src/run-archive/run-log-uploader.ts).
 *
 * RunLogUploader uses a cursor-based memory queue + 30-second batch upload
 * instead of console interception + regex parsing. This module is kept for
 * reference only and should not be used in new code.
 *
 * ConsoleLogCapture — global console.log/warn/error interceptor.
 *
 * Captures console output during workflow execution, extracts flowId from
 * the message text (ClawMind console output almost always includes
 * `flowId=xxx`), and buffers entries per-flow for async batch INSERT
 * into the run_logs table.
 *
 * Performance:
 * - When no flows are active (activeFlows.size === 0), capture() returns
 *   immediately after a single `if` check — zero overhead.
 * - When flows are active, each console call adds ~0.01ms for regex match
 *   + buffer push.
 * - DB writes are async and batched (flush on stopCapture or every 2s).
 * - Memory is bounded: each flow's buffer is capped at maxEntriesPerFlow.
 */
import type { IRunLogRepository, RunLogInsert } from "../db/repositories/types.js";

// ── Regex patterns for extracting structured info from console messages ──

// Matches: flowId=xxx, flow_id=xxx, flowId:"xxx", flowId: xxx, flow xxx, flow_xxx
const FLOW_ID_RE = /flow[_]?[Ii]d[:=]\s*"?([a-f0-9-]{8,})"?|flow\s+([a-f0-9-]{8,})\b/;
// Matches: [controller], [embedded-agent], etc.
const SOURCE_TAG_RE = /^\[([^\]]+)\]/;
// Matches: nodeId=xxx, node_id=xxx
const NODE_ID_RE = /node[_]?[Ii]d[:=]\s*"?([^\s,)"']+)/;

export function extractFlowId(message: string): string | null {
  const match = message.match(FLOW_ID_RE);
  if (!match) return null;
  // Group 1: flowId=xxx / flowId: xxx format
  // Group 2: flow xxx format
  return match[1] ?? match[2] ?? null;
}

export function extractSourceTag(message: string): string | null {
  const match = message.match(SOURCE_TAG_RE);
  return match?.[1] ?? null;
}

export function extractNodeId(message: string): string | null {
  const match = message.match(NODE_ID_RE);
  return match?.[1] ?? null;
}

function safeStringify(value: unknown): string {
  if (value === null || value === undefined) return String(value);
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value instanceof Error) return value.stack || value.message;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export class ConsoleLogCapture {
  private buffers = new Map<string, RunLogInsert[]>();
  private activeFlows = new Set<string>();
  private seqCounter = new Map<string, number>();
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private originalConsole: {
    log: typeof console.log;
    warn: typeof console.warn;
    error: typeof console.error;
  } | null = null;
  private repo: IRunLogRepository | null = null;
  private installed = false;
  private readonly maxEntriesPerFlow: number;

  // ── Diagnostic counters (reset on each flush cycle) ──
  private _diagCaptureCount = 0;
  private _diagLastCaptureLogTs = 0;
  private _diagFlushCount = 0;
  private _diagLastFlushLogTs = 0;
  private _diagDiscardCount = 0;
  private _diagLastDiscardLogTs = 0;
  /** Track which flows have already logged "first capture" to avoid spam. */
  private _diagFirstCaptureLogged = new Set<string>();

  constructor(options?: { maxEntriesPerFlow?: number }) {
    this.maxEntriesPerFlow = options?.maxEntriesPerFlow ?? 500;
  }

  setRepository(repo: IRunLogRepository): void {
    this.repo = repo;
    if (this.originalConsole) {
      this.originalConsole.log("[ConsoleLogCapture] repository set — run_logs persistence enabled");
    }
  }

  /** Install global console interceptors. Call once at engine init. */
  install(): void {
    if (this.installed) return;
    this.installed = true;
    this.originalConsole = {
      log: console.log.bind(console),
      warn: console.warn.bind(console),
      error: console.error.bind(console),
    };

    const self = this;

    console.log = function (...args: unknown[]) {
      self.capture("log", args);
      self.originalConsole!.log(...args);
    };
    console.warn = function (...args: unknown[]) {
      self.capture("warn", args);
      self.originalConsole!.warn(...args);
    };
    console.error = function (...args: unknown[]) {
      self.capture("error", args);
      self.originalConsole!.error(...args);
    };

    // Periodic flush for inactive flows (every 2 seconds)
    this.flushTimer = setInterval(() => this.flushInactive(), 2000);
    // Don't keep the process alive just for the timer
    if (this.flushTimer.unref) this.flushTimer.unref();

    // Diagnostic: log install status via original console (bypasses interceptor)
    this.originalConsole.log(
      `[ConsoleLogCapture] installed — repo=${this.repo != null} ` +
      `maxEntriesPerFlow=${this.maxEntriesPerFlow} flushInterval=2000ms`,
    );
  }

  /** Uninstall interceptors and restore original console methods. */
  uninstall(): void {
    if (!this.installed || !this.originalConsole) return;
    console.log = this.originalConsole.log;
    console.warn = this.originalConsole.warn;
    console.error = this.originalConsole.error;
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
    this.originalConsole = null;
    this.installed = false;
  }

  /** Mark a flowId as actively executing — start capturing its console output. */
  startCapture(flowId: string): void {
    const wasNew = !this.buffers.has(flowId);
    this.activeFlows.add(flowId);
    if (wasNew) {
      this.buffers.set(flowId, []);
    }
    // Diagnostic: log capture start (rate-limited: once per flow)
    if (wasNew && this.originalConsole) {
      this.originalConsole.log(
        `[ConsoleLogCapture] startCapture(flowId=${flowId.slice(0, 12)}...) ` +
        `activeFlows=${this.activeFlows.size} repo=${this.repo != null}`,
      );
    }
  }

  /**
   * Create a per-flow logger that writes directly into the buffer for the given flowId.
   * This bypasses the global console interceptor + regex parsing, providing 100% coverage
   * for logs emitted through this logger regardless of message format.
   *
   * Usage in controller:
   *   const flowLog = _consoleCapture.createFlowLogger(flowId);
   *   flowLog.log("[controller] NODE_EXECUTING ...");
   *   flowLog.warn("[controller] NODE_FAILED ...");
   *   flowLog.error("[controller] FLOW_CRASHED ...");
   */
  createFlowLogger(flowId: string): {
    log: (...args: unknown[]) => void;
    warn: (...args: unknown[]) => void;
    error: (...args: unknown[]) => void;
  } {
    // Ensure buffer exists
    if (!this.buffers.has(flowId)) {
      this.buffers.set(flowId, []);
    }

    const self = this;
    const write = (level: string, args: unknown[]): void => {
      const buffer = self.buffers.get(flowId);
      if (!buffer) return;

      const message = args.map((a) =>
        typeof a === "string" ? a : safeStringify(a),
      ).join(" ");

      const seq = (self.seqCounter.get(flowId) ?? 0) + 1;
      self.seqCounter.set(flowId, seq);

      buffer.push({
        flow_id: flowId,
        node_id: extractNodeId(message),
        level,
        source: extractSourceTag(message),
        message,
        timestamp: Date.now(),
        seq,
      });

      // Ring buffer: drop oldest entries if over limit
      if (buffer.length > self.maxEntriesPerFlow) {
        buffer.splice(0, buffer.length - self.maxEntriesPerFlow);
      }
    };

    return {
      log: (...args: unknown[]) => write("log", args),
      warn: (...args: unknown[]) => write("warn", args),
      error: (...args: unknown[]) => write("error", args),
    };
  }

  /**
   * Mark a flowId as completed — stop capturing and flush remaining buffer.
   * Returns a Promise that resolves when the flush completes, so callers can
   * await it to ensure data is persisted before the process exits.
   */
  stopCapture(flowId: string): Promise<void> {
    const buffer = this.buffers.get(flowId);
    const remaining = buffer?.length ?? 0;
    this.activeFlows.delete(flowId);
    if (this.originalConsole) {
      this.originalConsole.log(
        `[ConsoleLogCapture] stopCapture(flowId=${flowId.slice(0, 12)}...) ` +
        `bufferRemaining=${remaining} repo=${this.repo != null} ` +
        `totalCaptures=${this._diagCaptureCount} totalFlushes=${this._diagFlushCount} totalDiscards=${this._diagDiscardCount}`,
      );
    }
    return this.flush(flowId);
  }

  /**
   * Flush all buffers (active + inactive) to the database.
   * Call before process exit to ensure no data is lost.
   * Returns a Promise that resolves when all flushes complete.
   */
  async flushAll(): Promise<void> {
    const promises: Promise<void>[] = [];
    for (const fid of this.buffers.keys()) {
      promises.push(this.flush(fid));
    }
    await Promise.allSettled(promises);
  }

  /** Internal: capture a console call into the appropriate flow buffer. */
  private capture(level: string, args: unknown[]): void {
    // Fast path: no active flows → zero overhead
    if (this.activeFlows.size === 0) return;

    const message = args.map((a) =>
      typeof a === "string" ? a : safeStringify(a),
    ).join(" ");

    // Extract flowId from message text
    let flowId = extractFlowId(message);

    // Fallback: if regex didn't match but only one flow is active, attribute
    // the log to that flow. This covers console messages that don't include
    // flowId in their text (e.g. simple "[controller] ..." messages).
    if (!flowId && this.activeFlows.size === 1) {
      flowId = this.activeFlows.values().next().value ?? null;
    }

    if (!flowId || !this.buffers.has(flowId)) return;

    const buffer = this.buffers.get(flowId)!;
    const seq = (this.seqCounter.get(flowId) ?? 0) + 1;
    this.seqCounter.set(flowId, seq);

    buffer.push({
      flow_id: flowId,
      node_id: extractNodeId(message),
      level,
      source: extractSourceTag(message),
      message,
      timestamp: Date.now(),
      seq,
    });

    // Ring buffer: drop oldest entries if over limit
    if (buffer.length > this.maxEntriesPerFlow) {
      buffer.splice(0, buffer.length - this.maxEntriesPerFlow);
    }

    // ── Diagnostic: log first capture per flow + periodic heartbeat ──
    this._diagCaptureCount++;
    if (!this._diagFirstCaptureLogged.has(flowId)) {
      this._diagFirstCaptureLogged.add(flowId);
      if (this.originalConsole) {
        this.originalConsole.log(
          `[ConsoleLogCapture] first capture for flowId=${flowId.slice(0, 12)}... ` +
          `level=${level} activeFlows=${this.activeFlows.size} bufferSize=${buffer.length} repo=${this.repo != null}`,
        );
      }
    }
    // Periodic heartbeat: every 50 captures or every 30s
    const now = Date.now();
    if (this._diagCaptureCount % 50 === 0 || (now - this._diagLastCaptureLogTs > 30_000 && this._diagCaptureCount > 0)) {
      this._diagLastCaptureLogTs = now;
      if (this.originalConsole) {
        let totalBuffered = 0;
        for (const [, buf] of this.buffers) totalBuffered += buf.length;
        this.originalConsole.log(
          `[ConsoleLogCapture] heartbeat: captures=${this._diagCaptureCount} ` +
          `activeFlows=${this.activeFlows.size} totalBuffered=${totalBuffered} ` +
          `flushes=${this._diagFlushCount} discards=${this._diagDiscardCount} repo=${this.repo != null}`,
        );
      }
    }
  }

  /** Flush a single flow's buffer to the database. */
  private async flush(flowId: string): Promise<void> {
    const buffer = this.buffers.get(flowId);
    if (!buffer || buffer.length === 0 || !this.repo) {
      if (buffer && buffer.length > 0 && !this.repo) {
        // Log a warning when we have data but no repository — helps diagnose
        // the register()/buildDeps() timing issue in API mode.
        this._diagDiscardCount += buffer.length;
        const now = Date.now();
        if (now - this._diagLastDiscardLogTs > 10_000) {
          this._diagLastDiscardLogTs = now;
          if (this.originalConsole) {
            this.originalConsole.warn(
              `[ConsoleLogCapture] flush(${flowId.slice(0, 12)}...): ${buffer.length} entries DISCARDED — no repository set ` +
              `(total discards so far: ${this._diagDiscardCount})`,
            );
          }
        }
        this.buffers.set(flowId, []);
      }
      return;
    }
    // Clear buffer before async write to avoid double-flush
    const entries = buffer.splice(0, buffer.length);
    if (entries.length === 0) return;
    this._diagFlushCount++;
    try {
      const count = await this.repo.insertBatch(entries);
      if (this.originalConsole) {
        this.originalConsole.log(
          `[ConsoleLogCapture] flush(${flowId.slice(0, 12)}...): ${count}/${entries.length} entries WRITTEN ` +
          `(total flushes: ${this._diagFlushCount}, total discards: ${this._diagDiscardCount})`,
        );
      }
    } catch (err) {
      if (this.originalConsole) {
        this.originalConsole.warn(
          `[ConsoleLogCapture] flush(${flowId.slice(0, 12)}...): ${entries.length} entries FAILED — ` +
          `${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
  }

  /** Flush all non-active flow buffers (called by periodic timer). */
  private flushInactive(): void {
    let flushed = 0;
    let skipped = 0;
    for (const fid of this.buffers.keys()) {
      if (!this.activeFlows.has(fid)) {
        void this.flush(fid);
        flushed++;
      } else {
        skipped++;
      }
    }
    // Periodic diagnostic: log flush-inactive stats (every ~60s to avoid spam)
    if (this.originalConsole && (flushed > 0 || skipped > 0)) {
      const now = Date.now();
      if (now - this._diagLastFlushLogTs > 60_000) {
        this._diagLastFlushLogTs = now;
        let totalBuffered = 0;
        for (const [, buf] of this.buffers) totalBuffered += buf.length;
        this.originalConsole.log(
          `[ConsoleLogCapture] flushInactive: flushed=${flushed} skipped=${skipped} ` +
          `totalBuffered=${totalBuffered} activeFlows=${this.activeFlows.size} ` +
          `totalCaptures=${this._diagCaptureCount} totalFlushes=${this._diagFlushCount} totalDiscards=${this._diagDiscardCount}`,
        );
      }
    }
  }

  /** Clean up buffers for flowIds that are no longer active (memory management). */
  cleanup(activeFlowIds: Set<string>): void {
    for (const key of this.buffers.keys()) {
      if (!activeFlowIds.has(key)) {
        void this.flush(key);
        this.buffers.delete(key);
        this.seqCounter.delete(key);
      }
    }
  }
}

/**
 * Create and install a ConsoleLogCapture singleton with the given repository.
 *
 * This is the unified initialization entry point used by both index.ts (direct mode)
 * and mcp-server-factory.ts (MCP mode). It creates the interceptor, sets the
 * repository, installs global console hooks, and returns the instance.
 *
 * @param repo - Repository for persisting run_logs (API or direct DB).
 *               If null, logs are buffered in memory only (no persistence).
 * @param options - Optional configuration (maxEntriesPerFlow, etc.)
 * @returns The initialized ConsoleLogCapture instance (already installed).
 */
export function createAndInstallConsoleCapture(
  repo: IRunLogRepository | null,
  options?: { maxEntriesPerFlow?: number },
): ConsoleLogCapture {
  const capture = new ConsoleLogCapture(options);
  if (repo) capture.setRepository(repo);
  capture.install();
  return capture;
}
