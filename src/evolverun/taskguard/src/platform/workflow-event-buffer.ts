/**
 * Workflow Event Buffer — ring buffer of recent workflow progress events.
 *
 * When Claude Code's Channels API is unavailable (e.g., VS Code plugin mode,
 * CLI versions < 2.2), Channel 1 notifications are silently discarded.
 * This buffer provides a fallback: recent events are stored in-memory and
 * exposed via the `workflow_recent_events` MCP tool so Claude can poll
 * for progress updates.
 *
 * The buffer is per-MCP-server-instance (singleton per process), bounded
 * to MAX_EVENTS (200), and automatically prunes entries older than
 * EVENT_TTL_MS (5 minutes).
 *
 * Platform isolation: this module is only used by the MCP adapter path
 * (Claude Code / Hermes). OpenClaw uses injectChatMessage() and TeClaw
 * uses WebSocket chat.inject — neither path needs this buffer.
 *
 * @module platform/workflow-event-buffer
 */

/** A single buffered workflow event. */
export interface BufferedWorkflowEvent {
  /** Monotonically increasing sequence number. */
  seq: number;
  /** ISO 8601 timestamp when the event was buffered. */
  timestamp: string;
  /** Event type (progress, info, error, approval). */
  eventType: string;
  /** Human-readable message. */
  message: string;
  /** Flow ID (if available). */
  flowId?: string;
  /** Node ID (if available). */
  nodeId?: string;
  /** Workflow ID (if available). */
  workflowId?: string;
}

/** Maximum number of events to retain. */
const MAX_EVENTS = 200;

/** Events older than this are pruned. */
const EVENT_TTL_MS = 5 * 60 * 1000; // 5 minutes

/**
 * Ring buffer for recent workflow events.
 *
 * Uses a fixed-size array with head/tail pointers for O(1) push.
 * Pruning happens lazily on each push (drop entries older than TTL).
 */
class EventRingBuffer {
  private buffer: BufferedWorkflowEvent[] = [];
  private nextSeq = 1;

  /**
   * Push a new event into the buffer.
   * Returns the buffered event (with seq and timestamp assigned).
   */
  push(event: Omit<BufferedWorkflowEvent, "seq" | "timestamp">): BufferedWorkflowEvent {
    // Prune expired entries
    const cutoff = Date.now() - EVENT_TTL_MS;
    this.buffer = this.buffer.filter(e => new Date(e.timestamp).getTime() > cutoff);

    // Enforce max size (drop oldest)
    while (this.buffer.length >= MAX_EVENTS) {
      this.buffer.shift();
    }

    const entry: BufferedWorkflowEvent = {
      ...event,
      seq: this.nextSeq++,
      timestamp: new Date().toISOString(),
    };
    this.buffer.push(entry);
    return entry;
  }

  /**
   * Query recent events with optional filters.
   *
   * @param options.flowId - Filter by flow ID (optional)
   * @param options.sinceSeq - Return events with seq > this value (optional, for incremental polling)
   * @param options.limit - Maximum events to return (default 20, max 50)
   */
  query(options?: {
    flowId?: string;
    sinceSeq?: number;
    limit?: number;
    eventType?: string;
  }): BufferedWorkflowEvent[] {
    const limit = Math.min(options?.limit ?? 20, 50);
    let results = this.buffer;

    if (options?.flowId) {
      results = results.filter(e => e.flowId === options.flowId);
    }
    if (options?.sinceSeq !== undefined) {
      results = results.filter(e => e.seq > options.sinceSeq!);
    }
    if (options?.eventType) {
      results = results.filter(e => e.eventType === options.eventType);
    }

    // Return most recent first, limited
    return results.slice(-limit).reverse();
  }

  /** Get buffer statistics. */
  stats(): { count: number; oldestTimestamp: string | null; newestTimestamp: string | null; nextSeq: number } {
    return {
      count: this.buffer.length,
      oldestTimestamp: this.buffer[0]?.timestamp ?? null,
      newestTimestamp: this.buffer[this.buffer.length - 1]?.timestamp ?? null,
      nextSeq: this.nextSeq,
    };
  }

  /** Clear all events (for testing). */
  clear(): void {
    this.buffer = [];
    this.nextSeq = 1;
  }
}

// ── Singleton per process ──

/** Global event buffer instance. Shared across all MCP server instances. */
let globalBuffer: EventRingBuffer | undefined;

/**
 * Get the global event buffer (lazy singleton).
 * Created on first access so it doesn't allocate memory in OpenClaw mode.
 */
export function getWorkflowEventBuffer(): EventRingBuffer {
  if (!globalBuffer) {
    globalBuffer = new EventRingBuffer();
  }
  return globalBuffer;
}

/**
 * Push an event into the global buffer.
 * Convenience wrapper that silently no-ops if the buffer hasn't been initialized.
 */
export function bufferWorkflowEvent(
  message: string,
  eventType: string,
  meta?: { flowId?: string; nodeId?: string; workflowId?: string },
): BufferedWorkflowEvent | null {
  // Only buffer if someone has called getWorkflowEventBuffer() (i.e., MCP mode)
  if (!globalBuffer) return null;

  return globalBuffer.push({
    message,
    eventType,
    flowId: meta?.flowId,
    nodeId: meta?.nodeId,
    workflowId: meta?.workflowId,
  });
}