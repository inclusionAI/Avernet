/**
 * ErrorContextStore — stores error context entries per flow for intelligent retry.
 *
 * Each entry captures the error, node info, and optional KB search results.
 * FIFO eviction when max entries per flow is reached.
 *
 * Adapted from ClawMind's ErrorContextStore for ClawFlow.
 */

/** A pending error context entry to be injected into the next retry. */
export type PendingErrorContext = {
  /** The flow this error belongs to. */
  flowId: string;
  /** The node that failed. */
  nodeId: string;
  /** The error message. */
  error: string;
  /** The retry attempt number. */
  attempt: number;
  /** The original query used for KB search (if any). */
  query?: string;
  /** KB search results for error recovery, if available. */
  kbResults?: string;
  /** Timestamp of the error. */
  timestamp: number;
};

const DEFAULT_MAX_PER_FLOW = 20;

/**
 * Stores pending error context entries keyed by flowId.
 * Provides push, drain, and getLastQuery operations.
 */
export class ErrorContextStore {
  private readonly store = new Map<string, PendingErrorContext[]>();
  private readonly maxPerFlow: number;

  constructor(maxPerFlow: number = DEFAULT_MAX_PER_FLOW) {
    this.maxPerFlow = maxPerFlow;
  }

  /** Push an error context entry for the given flow. FIFO eviction if at capacity. */
  push(entry: PendingErrorContext): void {
    const entries = this.store.get(entry.flowId) ?? [];
    entries.push(entry);
    if (entries.length > this.maxPerFlow) {
      entries.shift();
    }
    this.store.set(entry.flowId, entries);
  }

  /** Drain and return all pending error context entries for a flow. */
  drain(flowId: string): PendingErrorContext[] {
    const entries = this.store.get(flowId) ?? [];
    this.store.delete(flowId);
    return entries;
  }

  /** Get the last query associated with a flow (for KB search context). */
  getLastQuery(flowId: string): string | undefined {
    const entries = this.store.get(flowId);
    if (!entries || entries.length === 0) return undefined;
    return entries[entries.length - 1].query;
  }

  /** Get all pending entries for a flow without draining. */
  peek(flowId: string): PendingErrorContext[] {
    return this.store.get(flowId) ?? [];
  }

  /** Clear all entries for a flow. */
  clear(flowId: string): void {
    this.store.delete(flowId);
  }

  /** Clear all entries. */
  clearAll(): void {
    this.store.clear();
  }
}