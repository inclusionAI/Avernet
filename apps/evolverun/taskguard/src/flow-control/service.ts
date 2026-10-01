/**
 * FlowControlService — business logic layer for flow control (concurrency limiting).
 *
 * Simplified: only perWorkflow scope remains. No global, no perExecutor.
 *
 * Key invariants:
 * - When config.enabled === false, all calls are no-ops (backward compatible).
 * - When maxConcurrent === 0 for a scope, acquisition always succeeds (unlimited).
 * - ReleaseHandle is idempotent — safe to call multiple times.
 * - Flow control NEVER modifies flow_runs.status — it only manages resources (slots, queue entries).
 */
import type { IFlowControlRepository, FlowControlQueueRow, FlowControlSlotRow } from "../db/repositories/types.js";
import type {
  FlowControlConfig,
  AcquireOptions,
  AcquireResult,
  ReleaseHandle,
  ScopeStatus,
  FlowControlAllStatus,
  ScopeKey,
} from "./types.js";
import { FLOW_CONTROL_DEFAULTS, getMaxConcurrentForScope, getQueueTimeoutMsForScope } from "./config.js";

/** Lease TTL in seconds — exported for use by acquireSlot and LeaseManager. */
export const LEASE_TTL_SECS = 60;

// ── ReleaseHandle implementations ──

/** Single-scope release handle. Idempotent: safe to call release() multiple times. */
class SingleReleaseHandle implements ReleaseHandle {
  released = false;

  constructor(
    private readonly repo: IFlowControlRepository,
    private readonly instanceId: string,
    private readonly scopeKey: ScopeKey,
    private readonly flowId: string,
    private readonly nodeId: string | null,
  ) {}

  release(): void {
    if (this.released) return;
    this.released = true;
    // M4 fix: Retry release on failure to prevent permanent slot leaks.
    // A single failed release causes a leaked slot that blocks the scope
    // until orphan cleanup detects it (which may take minutes).
    const MAX_RETRIES = 2;
    const BASE_DELAY_MS = 500;
    let attempt = 0;

    const tryRelease = async (): Promise<void> => {
      try {
        const released = await this.repo.releaseSlot(this.instanceId, this.scopeKey, this.flowId, this.nodeId);
        if (!released) {
          // Slot may have already been released by releaseAllForFlow — not an error.
          return;
        }
      } catch (err) {
        attempt++;
        if (attempt <= MAX_RETRIES) {
          const delay = BASE_DELAY_MS * Math.pow(2, attempt - 1);
          console.warn(`[flow-control] release handle error (attempt ${attempt}/${MAX_RETRIES + 1}), retrying in ${delay}ms:`, err);
          await new Promise(resolve => setTimeout(resolve, delay));
          return tryRelease();
        }
        console.error(`[flow-control] release handle error: failed after ${MAX_RETRIES + 1} attempts for scope=${this.scopeKey} flow=${this.flowId} node=${this.nodeId}:`, err);
      }
    };

    tryRelease().catch(() => {
      // Final catch — should not happen since tryRelease handles all errors internally,
      // but guard against unexpected rejections to prevent unhandled promise rejection.
    });
  }
}

/** No-op handle returned when flow control is disabled or unlimited. */
class NoOpReleaseHandle implements ReleaseHandle {
  released = false;
  release(): void {
    this.released = true;
  }
}

// ── FlowControlService ──

export class FlowControlService {
  private readonly config: FlowControlConfig;
  private readonly instanceId: string;
  private readonly repo: IFlowControlRepository;

  constructor(
    repo: IFlowControlRepository,
    config: FlowControlConfig = FLOW_CONTROL_DEFAULTS,
    instanceId: string = "default",
  ) {
    this.repo = repo;
    this.config = config;
    this.instanceId = instanceId;
  }

  // ── Public API ──

  /**
   * Non-blocking slot acquisition (single scope — perWorkflow only).
   *
   * - If config.enabled === false or maxConcurrent === 0: returns immediately with a no-op handle.
   * - Otherwise: attempts repo.acquireSlot. On success, returns a real handle.
   *   On failure, enqueues the request and returns { acquired: false, queuePosition }.
   */
  async tryAcquire(options: AcquireOptions): Promise<AcquireResult> {
    const maxConcurrent = getMaxConcurrentForScope(this.config, options.key);

    // Disabled or unlimited — always succeed
    if (!this.config.enabled) {
      console.log(`[flow-control] tryAcquire SKIP: flowControl.enabled=false, flowId=${options.flowId} scope=${options.key}`);
      return { acquired: true, handle: new NoOpReleaseHandle() };
    }
    if (maxConcurrent === 0) {
      console.log(`[flow-control] tryAcquire SKIP: maxConcurrent=0 (unlimited) for scope=${options.key}, flowId=${options.flowId}`);
      return { acquired: true, handle: new NoOpReleaseHandle() };
    }

    const now = Math.floor(Date.now() / 1000);
    const leaseExpiresAt = now + LEASE_TTL_SECS;
    const acquired = await this.repo.acquireSlot(
      {
        instanceId: this.instanceId,
        scopeKey: options.key,
        flowId: options.flowId,
        nodeId: options.nodeId ?? null,
        acquiredAt: now,
        sessionId: options.sessionId ?? null,
        leaseExpiresAt,
      },
      maxConcurrent,
    );

    if (acquired) {
      const handle = new SingleReleaseHandle(
        this.repo,
        this.instanceId,
        options.key,
        options.flowId,
        options.nodeId ?? null,
      );
      console.log(`[flow-control] ACQUIRED flowId=${options.flowId} scope=${options.key} node=${options.nodeId ?? "workflow"} maxConcurrent=${maxConcurrent} leaseExpiresAt=${leaseExpiresAt}`);
      return { acquired: true, handle };
    }

    // Failed to acquire — enqueue
    const timeoutMs = options.timeoutMs ?? getQueueTimeoutMsForScope(this.config, options.key);
    const queuePosition = await this.repo.enqueue({
      instanceId: this.instanceId,
      scopeKey: options.key,
      flowId: options.flowId,
      nodeId: options.nodeId ?? null,
      priority: options.priority ?? 0,
      status: "queued",
      enqueuedAt: now,
      dispatchAfter: null,
      expiresAt: timeoutMs > 0 ? now + Math.floor(timeoutMs / 1000) : null,
      payload: options.payload ?? null,
    });
    console.log(`[flow-control] QUEUED flowId=${options.flowId} scope=${options.key} node=${options.nodeId ?? "workflow"} queuePosition=${queuePosition} maxConcurrent=${maxConcurrent}`);

    return { acquired: false, queuePosition };
  }

  /**
   * Release a slot. Fire-and-forget (catches and logs errors).
   */
  release(scope: string, key: ScopeKey, flowId: string, nodeId?: string): void {
    this.repo
      .releaseSlot(this.instanceId, key, flowId, nodeId ?? null)
      .catch((err) => {
        console.error("[flow-control] release error:", err);
      });
  }

  /**
   * Release all slots + delete queue entries for a flow.
   * Retries on failure with exponential backoff to prevent permanent slot leaks.
   */
  releaseAllForFlow(flowId: string): void {
    console.log(`[flow-control] RELEASE_ALL flowId=${flowId} — releasing all slots and queue entries`);
    this.retryOperation(
      () => Promise.all([
        this.repo.releaseAllSlotsForFlow(this.instanceId, flowId),
        this.repo.deleteQueueEntriesForFlow(this.instanceId, flowId),
      ]),
      `releaseAllForFlow(${flowId})`,
    ).catch((err) => {
      // Final fallback: log clearly so operators can manually clean up
      console.error(`[flow-control] LEAK: releaseAllForFlow(${flowId}) failed permanently. Manual cleanup required. Error:`, err);
    });
  }

  /**
   * Retry an async operation with exponential backoff.
   * Used for release operations that must eventually succeed to avoid slot leaks.
   */
  private async retryOperation<T>(
    op: () => Promise<T>,
    label: string,
    maxRetries: number = 3,
    baseDelayMs: number = 1000,
  ): Promise<T> {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        return await op();
      } catch (err) {
        if (attempt === maxRetries) {
          console.error(`[flow-control] ${label} failed after ${maxRetries + 1} attempts:`, err);
          throw err;
        }
        const delay = baseDelayMs * Math.pow(2, attempt);
        console.warn(`[flow-control] ${label} failed (attempt ${attempt + 1}/${maxRetries + 1}), retrying in ${delay}ms:`, err);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
    throw new Error(`[flow-control] ${label}: unreachable`);
  }

  /**
   * Return ScopeStatus for a specific scope key.
   */
  async getStatus(key: ScopeKey): Promise<ScopeStatus> {
    const maxConcurrent = getMaxConcurrentForScope(this.config, key);
    const { running, queued } = await this.repo.getScopeStatus(this.instanceId, key);
    return {
      key,
      maxConcurrent,
      currentRunning: running,
      queuedCount: queued,
    };
  }

  /**
   * Return FlowControlAllStatus with active workflows.
   */
  async getAllStatus(): Promise<FlowControlAllStatus> {
    const activeScopeKeys = await this.repo.getActiveScopeKeys(this.instanceId);

    const workflowKeys: ScopeKey[] = [];
    for (const key of activeScopeKeys) {
      if (key.startsWith("workflow:")) {
        workflowKeys.push(key);
      }
    }

    const workflows = await Promise.all(workflowKeys.map((k) => this.getStatus(k)));

    return {
      workflows,
    };
  }

  /** Getter for instanceId. */
  getInstanceId(): string {
    return this.instanceId;
  }

  /** Getter for config. */
  getConfig(): FlowControlConfig {
    return this.config;
  }

  /**
   * Query queued items for monitoring.
   * Delegates to the repository with the service's instanceId.
   */
  async getQueueItems(scopeKey?: string, limit?: number): Promise<FlowControlQueueRow[]> {
    return this.repo.getQueueItems(this.instanceId, scopeKey, limit);
  }

  /**
   * Query active slots for monitoring.
   * Delegates to the repository with the service's instanceId.
   */
  async getSlots(scopeKey?: string): Promise<FlowControlSlotRow[]> {
    return this.repo.getSlots(this.instanceId, scopeKey);
  }
}