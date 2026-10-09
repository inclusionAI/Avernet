/**
 * RetryTracker and AutoRetryTracker for intelligent retry.
 *
 * RetryTracker: tracks retry counts by error signature (max 100 entries, FIFO eviction).
 * AutoRetryTracker: tracks auto-retry counts by retryKey (max 100 entries, FIFO eviction).
 *
 * Adapted from ClawMind's retry tracking for ClawFlow.
 */

/** Default maximum entries before FIFO eviction. */
const DEFAULT_MAX_ENTRIES = 100;

/**
 * Tracks retry counts by error signature.
 * Used to determine if a particular error pattern has been seen too many times.
 */
export class RetryTracker {
  private readonly counts = new Map<string, number>();
  private readonly order: string[] = [];
  private readonly maxEntries: number;

  constructor(maxEntries: number = DEFAULT_MAX_ENTRIES) {
    this.maxEntries = maxEntries;
  }

  /** Increment and return the retry count for a signature. */
  increment(signature: string): number {
    const current = this.counts.get(signature) ?? 0;
    const next = current + 1;
    this.counts.set(signature, next);

    // Track insertion order for FIFO eviction
    if (current === 0) {
      this.order.push(signature);
      if (this.order.length > this.maxEntries) {
        const oldest = this.order.shift()!;
        this.counts.delete(oldest);
      }
    }

    return next;
  }

  /** Get the current retry count for a signature (0 if not seen). */
  get(signature: string): number {
    return this.counts.get(signature) ?? 0;
  }

  /** Reset the count for a specific signature. */
  reset(signature: string): void {
    this.counts.delete(signature);
    const idx = this.order.indexOf(signature);
    if (idx >= 0) this.order.splice(idx, 1);
  }

  /** Clear all entries. */
  clear(): void {
    this.counts.clear();
    this.order.length = 0;
  }
}

/**
 * Tracks auto-retry counts by retry key (flowId:nodeId).
 * Used to enforce per-node auto-retry limits.
 */
export class AutoRetryTracker {
  private readonly counts = new Map<string, number>();
  private readonly order: string[] = [];
  private readonly maxEntries: number;

  constructor(maxEntries: number = DEFAULT_MAX_ENTRIES) {
    this.maxEntries = maxEntries;
  }

  /** Increment and return the auto-retry count for a key. */
  increment(retryKey: string): number {
    const current = this.counts.get(retryKey) ?? 0;
    const next = current + 1;
    this.counts.set(retryKey, next);

    if (current === 0) {
      this.order.push(retryKey);
      if (this.order.length > this.maxEntries) {
        const oldest = this.order.shift()!;
        this.counts.delete(oldest);
      }
    }

    return next;
  }

  /** Get the current auto-retry count for a key (0 if not seen). */
  get(retryKey: string): number {
    return this.counts.get(retryKey) ?? 0;
  }

  /** Reset the count for a specific key. */
  reset(retryKey: string): void {
    this.counts.delete(retryKey);
    const idx = this.order.indexOf(retryKey);
    if (idx >= 0) this.order.splice(idx, 1);
  }

  /** Clear all entries. */
  clear(): void {
    this.counts.clear();
    this.order.length = 0;
  }
}