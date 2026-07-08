// Session-owned runtime registry.
//
// Tracks per-session runtime state: active run, controller connection, orphan
// grace timers. This is the central authority for "which session is doing what
// and who controls it" — replacing the old connection-owned activeRuns model.

import { createLogger } from '../debug.js';

const log = createLogger('server');

const DEFAULT_ORPHAN_GRACE_MS = Number(process.env.SESSION_ORPHAN_GRACE_MS ?? 60_000);

export type ActiveRunRecord = {
  runId: string;
  sessionKey: string;
  abort: () => void;
  startedAt: number;
  state: 'running' | 'paused_for_interaction';
};

export type SessionRuntime = {
  sessionKey: string;
  controllerConnId?: string;
  activeRun?: ActiveRunRecord;
  lastActiveAt: number;
  graceTimer?: NodeJS.Timeout;
  /** R2: session 忙时缓存的待发送 chat.send 请求队列(FIFO,按发送顺序)。 */
  pendingMessages?: PendingChatMessage[];
};

/**
 * 一条在 session 忙时被缓存的 chat.send 请求。
 *
 * 仅保存重新触发所需的原始 params(message/model/permissionMode/cwd/contextTurns
 * 等),不持有 ConnectionContext —— flush 时复用当前完成 run 的 controller ctx 触发,
 * 避免缓存的连接断开导致的悬挂引用。
 */
export type PendingChatMessage = {
  params: Record<string, unknown>;
  enqueuedAt: number;
};

export type SessionStatus = {
  sessionKey: string;
  attached: boolean;
  controller: boolean;
  processing: boolean;
  activeRun?: {
    runId: string;
    state: 'running' | 'paused_for_interaction';
  };
  pendingInteractionCount: number;
  updatedAt: string;
};

export class SessionRuntimeRegistry {
  private readonly sessions = new Map<string, SessionRuntime>();
  private readonly orphanGraceMs: number;
  /** Callback invoked when orphan grace expires and session resources must be cleaned. */
  private readonly onOrphanCleanup?: (sessionKey: string) => void;

  constructor(opts?: {
    orphanGraceMs?: number;
    onOrphanCleanup?: (sessionKey: string) => void;
  }) {
    this.orphanGraceMs = opts?.orphanGraceMs ?? DEFAULT_ORPHAN_GRACE_MS;
    this.onOrphanCleanup = opts?.onOrphanCleanup;
  }

  private ensure(sessionKey: string): SessionRuntime {
    let rt = this.sessions.get(sessionKey);
    if (!rt) {
      rt = { sessionKey, lastActiveAt: Date.now() };
      this.sessions.set(sessionKey, rt);
    }
    return rt;
  }

  // ---- Attach / Detach ----

  attachConnection(sessionKey: string, connId: string): SessionRuntime {
    const rt = this.ensure(sessionKey);
    // If another connection is controller, handoff
    if (rt.controllerConnId && rt.controllerConnId !== connId) {
      log.debug('session:controller-handoff', {
        sessionKey,
        previousController: rt.controllerConnId,
        newController: connId,
      });
    }
    rt.controllerConnId = connId;
    rt.lastActiveAt = Date.now();
    // Cancel orphan cleanup if active
    this.cancelOrphanCleanup(sessionKey);
    log.debug('session:attached', { sessionKey, connId });
    return rt;
  }

  detachConnection(sessionKey: string, connId: string): void {
    const rt = this.sessions.get(sessionKey);
    if (!rt) return;
    if (rt.controllerConnId === connId) {
      rt.controllerConnId = undefined;
      log.debug('session:detached', { sessionKey, connId });
      // If session has active work, start orphan grace
      if (rt.activeRun) {
        this.scheduleOrphanCleanup(sessionKey);
      } else {
        // No active run, clean up immediately
        this.sessions.delete(sessionKey);
      }
    }
  }

  detachAllForConnection(connId: string): void {
    for (const [ sessionKey, rt ] of this.sessions) {
      if (rt.controllerConnId === connId) {
        this.detachConnection(sessionKey, connId);
      }
    }
  }

  // ---- Controller ----

  getController(sessionKey: string): string | undefined {
    return this.sessions.get(sessionKey)?.controllerConnId;
  }

  isController(sessionKey: string, connId: string): boolean {
    return this.sessions.get(sessionKey)?.controllerConnId === connId;
  }

  isAttached(sessionKey: string, connId: string): boolean {
    return this.sessions.get(sessionKey)?.controllerConnId === connId;
  }

  // ---- Active Run ----

  registerRun(sessionKey: string, run: ActiveRunRecord): void {
    const rt = this.ensure(sessionKey);
    rt.activeRun = run;
    rt.lastActiveAt = Date.now();
    log.debug('session:run-registered', { sessionKey, runId: run.runId });
  }

  getActiveRun(sessionKey: string): ActiveRunRecord | undefined {
    return this.sessions.get(sessionKey)?.activeRun;
  }

  getActiveRunByRunId(runId: string): ActiveRunRecord | undefined {
    for (const rt of this.sessions.values()) {
      if (rt.activeRun?.runId === runId) return rt.activeRun;
    }
    return undefined;
  }

  updateRunState(sessionKey: string, state: 'running' | 'paused_for_interaction'): void {
    const rt = this.sessions.get(sessionKey);
    if (rt?.activeRun) {
      rt.activeRun.state = state;
      rt.lastActiveAt = Date.now();
    }
  }

  completeRun(sessionKey: string, runId: string): void {
    const rt = this.sessions.get(sessionKey);
    if (!rt) return;
    if (rt.activeRun?.runId === runId) {
      rt.activeRun = undefined;
      rt.lastActiveAt = Date.now();
      log.debug('session:run-completed', { sessionKey, runId });
      // If no controller, clean up session entry
      if (!rt.controllerConnId) {
        this.cancelOrphanCleanup(sessionKey);
        this.sessions.delete(sessionKey);
      }
    }
  }

  removeRun(sessionKey: string, runId: string): void {
    this.completeRun(sessionKey, runId);
  }

  isSessionBusy(sessionKey: string): boolean {
    const rt = this.sessions.get(sessionKey);
    return rt?.activeRun != null;
  }

  // ---- Pending message queue (R2) ----

  /**
   * 入队一条在 session 忙时收到的 chat.send 请求。返回入队后的队列长度。
   */
  enqueuePendingMessage(sessionKey: string, msg: PendingChatMessage): number {
    const rt = this.ensure(sessionKey);
    if (!rt.pendingMessages) rt.pendingMessages = [];
    rt.pendingMessages.push(msg);
    rt.lastActiveAt = Date.now();
    log.debug('[CC][queue] enqueue', { sessionKey, queuedCount: rt.pendingMessages.length });
    return rt.pendingMessages.length;
  }

  /**
   * 取出并清空该 session 的全部 pending 消息(按入队顺序返回)。
   */
  drainPendingMessages(sessionKey: string): PendingChatMessage[] {
    const rt = this.sessions.get(sessionKey);
    if (!rt || !rt.pendingMessages || rt.pendingMessages.length === 0) return [];
    const drained = rt.pendingMessages;
    rt.pendingMessages = [];
    log.debug('[CC][queue] drain', { sessionKey, count: drained.length });
    return drained;
  }

  /**
   * 清空该 session 的 pending 队列(abort 时调用),返回被丢弃的条数。
   */
  clearPendingMessages(sessionKey: string): number {
    const rt = this.sessions.get(sessionKey);
    const count = rt?.pendingMessages?.length ?? 0;
    if (rt?.pendingMessages) rt.pendingMessages = [];
    if (count > 0) log.debug('[CC][queue] drop', { sessionKey, count });
    return count;
  }

  pendingMessageCount(sessionKey: string): number {
    return this.sessions.get(sessionKey)?.pendingMessages?.length ?? 0;
  }

  // ---- Orphan Grace ----

  scheduleOrphanCleanup(sessionKey: string): void {
    const rt = this.sessions.get(sessionKey);
    if (!rt) return;
    this.cancelOrphanCleanup(sessionKey);
    log.debug('session:orphan-grace-started', { sessionKey, graceMs: this.orphanGraceMs });
    rt.graceTimer = setTimeout(() => {
      // Re-check: session may have been re-attached
      const current = this.sessions.get(sessionKey);
      if (!current) return;
      if (current.controllerConnId) {
        log.debug('session:orphan-grace-cancelled-reassigned', { sessionKey });
        return;
      }
      if (!current.activeRun) {
        // No active run, just clean up
        this.sessions.delete(sessionKey);
        return;
      }
      log.debug('session:orphan-grace-expired', { sessionKey, runId: current.activeRun.runId });
      // Invoke cleanup callback
      this.onOrphanCleanup?.(sessionKey);
      // Abort the active run
      try {
        current.activeRun.abort();
      } catch (err) {
        log.error('session:orphan-abort-threw', { sessionKey, error: (err as Error).message });
      }
      this.sessions.delete(sessionKey);
    }, this.orphanGraceMs);
    rt.graceTimer.unref?.();
  }

  cancelOrphanCleanup(sessionKey: string): void {
    const rt = this.sessions.get(sessionKey);
    if (rt?.graceTimer) {
      clearTimeout(rt.graceTimer);
      rt.graceTimer = undefined;
      log.debug('session:orphan-grace-cancelled', { sessionKey });
    }
  }

  // ---- Status ----

  getStatus(sessionKey: string, pendingInteractionCount = 0, connId?: string): SessionStatus {
    const rt = this.sessions.get(sessionKey);
    const isCtrl = connId ? rt?.controllerConnId === connId : rt?.controllerConnId != null;
    return {
      sessionKey,
      attached: connId ? isCtrl : rt?.controllerConnId != null,
      controller: isCtrl,
      processing: rt?.activeRun != null,
      activeRun: rt?.activeRun
        ? { runId: rt.activeRun.runId, state: rt.activeRun.state }
        : undefined,
      pendingInteractionCount,
      updatedAt: new Date(rt?.lastActiveAt ?? Date.now()).toISOString(),
    };
  }

  // ---- Lifecycle ----

  has(sessionKey: string): boolean {
    return this.sessions.has(sessionKey);
  }

  size(): number {
    return this.sessions.size;
  }

  /** Stop all grace timers. Call on server shutdown. */
  shutdown(): void {
    for (const rt of this.sessions.values()) {
      if (rt.graceTimer) clearTimeout(rt.graceTimer);
      if (rt.activeRun) {
        try { rt.activeRun.abort(); } catch { /* best effort */ }
      }
    }
    this.sessions.clear();
  }
}
