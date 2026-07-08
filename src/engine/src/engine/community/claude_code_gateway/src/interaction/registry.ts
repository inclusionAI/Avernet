// Unified pending-interaction registry.
//
// Replaces the file-level `pendingInteractions` Map + `interactionScannerTimer`
// that previously lived in server.ts. One registry is constructed per gateway
// server instance and injected into handlers / ConnectionContext.
//
// Session-aware: interactions are owned by sessionKey, not connId. connId is
// retained as createdByConnId for audit purposes only.

import { createLogger } from '../debug.js';
import type { PendingInteraction } from './types.js';

const log = createLogger('server');

const DEFAULT_SCAN_INTERVAL_MS = 5_000;

export class PendingInteractionRegistry {
  private readonly pending = new Map<string, PendingInteraction>();
  private scannerTimer: NodeJS.Timeout | null = null;

  constructor(private readonly scanIntervalMs: number = DEFAULT_SCAN_INTERVAL_MS) {}

  /** Register a pending interaction.
   *  @return {boolean} false if an interaction with the same ID already exists (duplicate).
   *  Multiple pending interactions per run are allowed — the SDK suspend/resume path
   *  can produce concurrent canUseTool calls when Claude issues several tool_use blocks at once. */
  register(record: PendingInteraction): boolean {
    if (this.pending.has(record.interactionId)) {
      log.warn('interaction:duplicate-id', {
        interactionId: record.interactionId,
        runId: record.runId,
      });
      record.rejecter?.(new Error('Interaction already registered'));
      return false;
    }

    // Initialize runtime fields
    if (!record.createdAtMs) {
      record.createdAtMs = Date.now();
    }
    if (!record.status) {
      record.status = 'pending';
    }

    this.pending.set(record.interactionId, record);
    this.ensureScanner();
    return true;
  }

  /**
   * Resolve a pending interaction by ID.
   * @return {boolean} true if resolved successfully, false if not found or already resolved
   */
  resolve(id: string, input: import('./types.js').ResolvedInteractionInput): boolean {
    const rec = this.pending.get(id);
    if (!rec) {
      log.warn('interaction:resolve:not-found', { id, pendingCount: this.pending.size });
      return false;
    }
    if (rec.status !== 'pending') {
      log.warn('interaction:resolve:already-resolved', { id, status: rec.status });
      return false;
    }

    log.debug('interaction:resolve:success', { id, kind: rec.kind, decision: input.decision });
    rec.status = 'resolved';
    this.pending.delete(id);

    if (rec.resolver) {
      try {
        rec.resolver(input);
      } catch (err) {
        log.error('interaction:resolver-threw', { id, error: (err as Error).message });
      }
    }
    return true;
  }

  /**
   * Reject a pending interaction by ID.
   * @return {boolean} true if rejected successfully, false if not found or already resolved
   */
  reject(id: string, error: Error): boolean {
    const rec = this.pending.get(id);
    if (!rec) return false;
    if (rec.status !== 'pending') {
      log.warn('interaction:already-resolved', { id, status: rec.status });
      return false;
    }

    rec.status = rec.status === 'pending' ? 'cancelled' : rec.status;
    this.pending.delete(id);

    if (rec.rejecter) {
      try {
        rec.rejecter(error);
      } catch (err) {
        log.error('interaction:rejecter-threw', { id, error: (err as Error).message });
      }
    }
    return true;
  }

  /**
   * Check if a run has an active (pending) interaction.
   */
  hasActiveRunInteraction(runId: string): boolean {
    return this.getActiveForRun(runId) !== undefined;
  }

  /**
   * Get the active interaction for a run.
   */
  getActiveForRun(runId: string): PendingInteraction | undefined {
    for (const rec of this.pending.values()) {
      if (rec.runId === runId && rec.status === 'pending') {
        return rec;
      }
    }
    return undefined;
  }

  take(id: string): PendingInteraction | undefined {
    const rec = this.pending.get(id);
    if (rec) this.pending.delete(id);
    return rec;
  }

  peek(id: string): PendingInteraction | undefined {
    return this.pending.get(id);
  }

  delete(id: string): void {
    this.pending.delete(id);
  }

  // ---- Session-aware query methods ----

  /** Get all pending interactions for a session. */
  listForSession(sessionKey: string): PendingInteraction[] {
    const result: PendingInteraction[] = [];
    for (const rec of this.pending.values()) {
      if (rec.sessionKey === sessionKey) result.push(rec);
    }
    return result.sort((a, b) => (a.createdAtMs ?? 0) - (b.createdAtMs ?? 0));
  }

  /** Cancel all pending interactions for a session. */
  cancelForSession(sessionKey: string): void {
    for (const [ id, rec ] of this.pending) {
      if (rec.sessionKey === sessionKey) {
        this.pending.delete(id);
        if (rec.status === 'pending' && rec.rejecter) {
          rec.status = 'cancelled';
          try {
            rec.rejecter(new Error('Session cancelled'));
          } catch (err) {
            log.error('interaction:session-cancel-rejecter-threw', { id, error: (err as Error).message });
          }
        }
      }
    }
  }

  /** Cancel all pendings tied to a given runId (no connId restriction). */
  takeForRun(runId: string): PendingInteraction[] {
    const taken: PendingInteraction[] = [];
    for (const [ id, rec ] of this.pending) {
      if (rec.runId === runId) {
        this.pending.delete(id);
        taken.push(rec);
        if (rec.status === 'pending' && rec.rejecter) {
          rec.status = 'cancelled';
          try {
            rec.rejecter(new Error('Aborted'));
          } catch (err) {
            log.error('interaction:abort-rejecter-threw', { id, error: (err as Error).message });
          }
        }
      }
    }
    return taken;
  }

  // ---- Legacy compatibility methods ----

  /** @deprecated Use cancelForSession instead. Kept for backward compatibility. */
  cancelForConnection(connId: string): void {
    for (const [ id, rec ] of this.pending) {
      if (rec.createdByConnId === connId) {
        this.pending.delete(id);
        if (rec.status === 'pending' && rec.rejecter) {
          rec.status = 'cancelled';
          try {
            rec.rejecter(new Error('Connection closed'));
          } catch (err) {
            log.error('interaction:disconnect-rejecter-threw', { id, error: (err as Error).message });
          }
        }
      }
    }
  }

  /** @deprecated Use takeForRun(runId) instead. Kept for backward compatibility. */
  takeForRunWithConnId(connId: string, runId: string): PendingInteraction[] {
    const taken: PendingInteraction[] = [];
    for (const [ id, rec ] of this.pending) {
      if (rec.createdByConnId === connId && rec.runId === runId) {
        this.pending.delete(id);
        taken.push(rec);
        if (rec.status === 'pending' && rec.rejecter) {
          rec.status = 'cancelled';
          try {
            rec.rejecter(new Error('Aborted'));
          } catch (err) {
            log.error('interaction:abort-rejecter-threw', { id, error: (err as Error).message });
          }
        }
      }
    }
    return taken;
  }

  /** @deprecated Use listForSession instead. Kept for backward compatibility. */
  getForConnection(connId: string): PendingInteraction[] {
    const result: PendingInteraction[] = [];
    for (const rec of this.pending.values()) {
      if (rec.createdByConnId === connId) result.push(rec);
    }
    return result;
  }

  /** Count pending interactions for a session. */
  countForSession(sessionKey: string): number {
    let count = 0;
    for (const rec of this.pending.values()) {
      if (rec.sessionKey === sessionKey && rec.status === 'pending') count++;
    }
    return count;
  }

  size(): number {
    return this.pending.size;
  }

  stopScanner(): void {
    if (this.scannerTimer) {
      clearInterval(this.scannerTimer);
      this.scannerTimer = null;
    }
  }

  private ensureScanner(): void {
    if (this.scannerTimer) return;
    this.scannerTimer = setInterval(() => {
      const now = Date.now();
      for (const [ id, rec ] of this.pending) {
        if (rec.expiresAtMs <= now) {
          this.pending.delete(id);
          const wasPending = rec.status === 'pending';
          rec.status = 'expired';
          try { rec.onExpire?.(); } catch (err) {
            log.error('interaction:expire-threw', { id, error: (err as Error).message });
          }
          if (wasPending && rec.rejecter) {
            try {
              rec.rejecter(new Error('Interaction expired'));
            } catch (err) {
              log.error('interaction:expire-rejecter-threw', { id, error: (err as Error).message });
            }
          }
        }
      }
      if (this.pending.size === 0) this.stopScanner();
    }, this.scanIntervalMs);
    this.scannerTimer.unref?.();
  }
}
