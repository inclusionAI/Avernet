/**
 * Per-session serial injection queue with a global concurrency cap.
 *
 * Problem this solves
 * -------------------
 * `chat.inject` is delivered by spawning an `openclaw gateway call chat.inject`
 * subprocess. Under concurrency that subprocess systematically hits its 10s
 * timeout (measured p50 ≈ 10s, p90 ≈ 12s, up to 26s at high load). Previously
 * each embedded-agent loop/final event `await`ed this inject ON the agent's
 * event-callback path, so:
 *   - every agent step was throttled by ~10s of UI-display latency, and
 *   - at peak, 83 inject subprocesses ran at once (one per in-flight event).
 *
 * What this queue guarantees
 * --------------------------
 *   - {@link enqueueInject} returns immediately — it NEVER blocks the caller,
 *     so the agent event loop is decoupled from inject latency.
 *   - Tasks for the same key run strictly serially → at most ONE inject
 *     subprocess per session in flight (preserves transcript order too).
 *   - At most {@link MAX_ACTIVE_LANES} lanes drain concurrently — a HARD global
 *     ceiling on simultaneous inject subprocesses regardless of how many
 *     sessions are active. Extra lanes wait FIFO for a slot.
 *   - Each per-key lane is bounded ({@link MAX_QUEUE_PER_KEY}); on overflow the
 *     OLDEST *droppable* task is discarded (progress/loop messages are ephemeral).
 *     Non-droppable tasks (final output) are retained.
 *   - Coalescing: when multiple droppable tasks with a `coalesceMessage` arrive
 *     in a lane within a short time window, they are merged into a single inject
 *     to reduce subprocess overhead (see {@link COALESCE_WINDOW_MS}).
 *
 * Net effect: memory is bounded (≤ MAX_QUEUE_PER_KEY × lanes), and concurrent
 * subprocesses are bounded (≤ MAX_ACTIVE_LANES). The queue cannot be "flooded".
 *
 * Safety
 * ------
 * Injection is display-only: nothing downstream reads its result and failures
 * were already swallowed by the caller, so detaching it is correctness-safe.
 *
 * @module inject-queue
 */

/** Options controlling how an enqueued inject behaves under back-pressure. */
export interface EnqueueInjectOptions {
  /**
   * Droppable tasks MAY be discarded (oldest-first) when the lane overflows.
   * Use `true` for ephemeral progress/loop messages, `false` for final output.
   * Default: `false`.
   */
  droppable?: boolean;
  /**
   * Coalesce message — when provided alongside `coalesceSender`, this task
   * becomes eligible for message coalescing. The queue will merge multiple
   * coalesce-eligible tasks within {@link COALESCE_WINDOW_MS} into a single
   * message and send it via `coalesceSender`, reducing subprocess count.
   *
   * Tasks without `coalesceMessage` are never coalesced and always run their
   * original `run` closure.
   */
  coalesceMessage?: string;
  /**
   * Sender function for coalesced messages. When coalescing fires, the queue
   * calls `coalesceSender(mergedMessage, idempotencyKey)` instead of running
   * each task's `run` closure individually. The `idempotencyKey` passed is
   * that of the first task in the coalesced batch.
   */
  coalesceSender?: (message: string, idempotencyKey: string) => Promise<void>;
  /**
   * Idempotency key for coalescing — paired with `coalesceMessage` and
   * `coalesceSender`.
   */
  coalesceIdempotencyKey?: string;
}

/**
 * Callback invoked when one or more droppable tasks are discarded due to
 * back-pressure. The callback receives a summary message suitable for
 * injection into the chat stream so the user is aware that progress
 * messages were skipped.
 */
export type DropNotifier = (summary: string) => void;

interface QueuedTask {
  /** Original run closure — always present. */
  run: () => Promise<void>;
  /** Whether this task can be dropped under back-pressure. */
  droppable: boolean;
  /** Enqueue timestamp — used for coalescing window. */
  enqueuedAt: number;
  /** Message text for coalescing. If absent, task is never coalesced. */
  coalesceMessage?: string;
  /** Sender for coalesced messages. */
  coalesceSender?: (message: string, idempotencyKey: string) => Promise<void>;
  /** Idempotency key for coalescing. */
  coalesceIdempotencyKey?: string;
}

interface Lane {
  items: QueuedTask[];
  /** Currently draining (holds a global slot). */
  active: boolean;
  /** Parked in the global waiting list because no slot was free. */
  waiting: boolean;
}

/**
 * Max pending tasks per key before back-pressure discards the oldest droppable
 * task. Overridable via `CLAWMIND_MAX_QUEUE_PER_KEY` env var.
 *
 * Previous value was 8; raised to 20 to significantly reduce the chance of
 * losing progress messages under moderate load while still bounding memory.
 */
const MAX_QUEUE_PER_KEY = (() => {
  const raw = process.env.CLAWMIND_MAX_QUEUE_PER_KEY;
  const n = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 20;
})();

/**
 * Hard global ceiling on lanes draining at once = max concurrent chat.inject
 * subprocesses across ALL sessions. Overridable via env for tuning.
 */
const MAX_ACTIVE_LANES = (() => {
  const raw = process.env.CLAWMIND_MAX_INJECT_CONCURRENCY;
  const n = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 16;
})();

/**
 * Time window (ms) within which consecutive coalesce-eligible droppable tasks
 * in the same lane are merged into a single inject. When the drain loop
 * encounters a run of 2+ such tasks whose enqueue timestamps all fall within
 * this window, their messages are concatenated and sent as one call.
 *
 * This halves the subprocess count for burst-heavy flows (e.g., multi-node
 * sequential transitions that each fire a start + succeed notification).
 */
const COALESCE_WINDOW_MS = 2000;

/**
 * Maximum number of messages to coalesce into a single inject.
 * Prevents the merged message from exceeding chat.inject's payload limits.
 */
const MAX_COALESCE_BATCH = 5;

/** Log a back-pressure warning once every N drops to avoid log spam. */
const DROP_LOG_EVERY = 10;

const lanes = new Map<string, Lane>();
const waitingKeys: string[] = [];
let activeLanes = 0;
let droppedTotal = 0;

/**
 * Every lane key ever passed to {@link enqueueInject} since the last
 * {@link resetInjectQueue}. Read-only diagnostics: lets tests assert the
 * per-flow lane unification (one key per flow, no stray sessionKey lane)
 * deterministically, without racing the async drain.
 */
const enqueuedLaneKeys = new Set<string>();

/**
 * Optional drop notifier — set via {@link setDropNotifier}. When set, the
 * queue calls it with a human-readable summary whenever droppable tasks are
 * discarded, so a "⚠️ N 条进度消息因负载过高被跳过" notice can be injected
 * into the chat stream.
 */
let dropNotifier: DropNotifier | null = null;

/**
 * Register a drop notifier. The notifier is called (rate-limited) with a
 * summary string when back-pressure forces task discards.
 */
export function setDropNotifier(fn: DropNotifier | null): void {
  dropNotifier = fn;
}

/**
 * Schedule an injection to run on the given key's serial lane.
 *
 * Returns synchronously (void) — the task runs in the background. Errors thrown
 * by `run` are swallowed so one bad inject never stalls the lane.
 *
 * @param key       Serialization key — use the sessionKey so all injects for one
 *                  session preserve order and never overlap.
 * @param run       Thunk that performs the actual inject (e.g. calls injectChatMessage).
 * @param options   {@link EnqueueInjectOptions}
 */
export function enqueueInject(
  key: string,
  run: () => Promise<void>,
  options: EnqueueInjectOptions = {},
): void {
  let lane = lanes.get(key);
  if (!lane) {
    lane = { items: [], active: false, waiting: false };
    lanes.set(key, lane);
  }
  const task: QueuedTask = {
    run,
    droppable: options.droppable ?? false,
    enqueuedAt: Date.now(),
    coalesceMessage: options.coalesceMessage,
    coalesceSender: options.coalesceSender,
    coalesceIdempotencyKey: options.coalesceIdempotencyKey,
  };
  lane.items.push(task);
  enqueuedLaneKeys.add(key);

  // Back-pressure: keep the lane bounded by discarding the oldest droppable task.
  // Non-droppable tasks (final output) are retained even over the cap.
  if (lane.items.length > MAX_QUEUE_PER_KEY) {
    const idx = lane.items.findIndex((t) => t.droppable);
    if (idx !== -1) {
      lane.items.splice(idx, 1);
      droppedTotal += 1;
      console.warn(
        `[inject-queue] back-pressure: dropped ${droppedTotal} droppable inject(s) so far ` +
        `(key=${key}, laneLen=${lane.items.length}, maxPerKey=${MAX_QUEUE_PER_KEY})`,
      );
      // Notify the user that messages were skipped (rate-limited by DROP_LOG_EVERY).
      if (dropNotifier && droppedTotal % DROP_LOG_EVERY === 1) {
        const summary = `⚠️ 因系统负载较高，部分进度消息已被跳过（累计 ${droppedTotal} 条）。完整执行记录已保存至日志。`;
        try { dropNotifier(summary); } catch { /* notifier must never throw */ }
      }
    }
  }

  tryStart(key);
}

/** Start draining a lane if it isn't already, respecting the global slot cap. */
function tryStart(key: string): void {
  const lane = lanes.get(key);
  if (!lane || lane.active || lane.items.length === 0) return;
  if (activeLanes >= MAX_ACTIVE_LANES) {
    if (!lane.waiting) {
      lane.waiting = true;
      waitingKeys.push(key);
    }
    return;
  }
  lane.waiting = false;
  lane.active = true;
  activeLanes += 1;
  void drain(key);
}

/**
 * Drain a single key's lane serially until empty, then release its slot.
 *
 * Before executing each task, checks whether the next few tasks are eligible
 * for coalescing (all droppable, all have coalesceMessage, all enqueued within
 * {@link COALESCE_WINDOW_MS}). If so, merges their messages and sends them as
 * a single inject call via the shared coalesceSender.
 */
async function drain(key: string): Promise<void> {
  const lane = lanes.get(key);
  if (!lane) return;
  while (lane.items.length > 0) {
    // ── Coalescing ──
    // Try to merge consecutive coalesce-eligible droppable tasks within the
    // coalesce window into a single inject to reduce subprocess count.
    const coalesced = tryCoalesce(lane);
    if (coalesced) {
      try {
        await coalesced.sender(coalesced.mergedMessage, coalesced.idempotencyKey);
      } catch {
        // Injection is display-only — swallow.
      }
    } else {
      const task = lane.items.shift() as QueuedTask;
      try {
        await task.run();
      } catch {
        // Injection is display-only — swallow so one failing inject
        // never blocks the rest of the lane.
      }
    }
  }
  lane.active = false;
  activeLanes -= 1;
  // No `await` between the loop exit and here, so no enqueue can interleave:
  // an empty lane is safe to release.
  lanes.delete(key);
  pumpWaiting();
}

/**
 * Check if the head of the lane can be coalesced with subsequent coalesce-
 * eligible droppable tasks. If eligible, removes the coalesced tasks from the
 * lane and returns a merged descriptor. Returns `null` if no coalescing is
 * possible.
 */
function tryCoalesce(lane: Lane): {
  sender: (message: string, idempotencyKey: string) => Promise<void>;
  mergedMessage: string;
  idempotencyKey: string;
} | null {
  if (lane.items.length < 2) return null;

  const first = lane.items[0];
  if (!first || !first.droppable || !first.coalesceMessage || !first.coalesceSender) {
    return null;
  }

  const windowStart = first.enqueuedAt;
  let endIdx = 1;
  while (
    endIdx < lane.items.length &&
    endIdx < MAX_COALESCE_BATCH &&
    lane.items[endIdx].droppable &&
    lane.items[endIdx].coalesceMessage &&
    lane.items[endIdx].coalesceSender === first.coalesceSender &&
    lane.items[endIdx].enqueuedAt - windowStart <= COALESCE_WINDOW_MS
  ) {
    endIdx++;
  }

  // Need at least 2 tasks to coalesce
  if (endIdx < 2) return null;

  // Extract the tasks to coalesce
  const toMerge = lane.items.splice(0, endIdx);

  // Build merged message: concatenate with separator
  const messages = toMerge.map((t) => t.coalesceMessage!).filter(Boolean);
  const mergedMessage = messages.join("\n\n---\n\n");

  return {
    sender: first.coalesceSender,
    mergedMessage,
    idempotencyKey: first.coalesceIdempotencyKey ?? "",
  };
}

/** Promote parked lanes into freed global slots (FIFO). */
function pumpWaiting(): void {
  while (waitingKeys.length > 0 && activeLanes < MAX_ACTIVE_LANES) {
    const key = waitingKeys.shift() as string;
    const lane = lanes.get(key);
    if (!lane) continue; // lane drained/released while parked
    lane.waiting = false;
    if (!lane.active && lane.items.length > 0) {
      lane.active = true;
      activeLanes += 1;
      void drain(key);
    }
  }
}

/** Snapshot of queue state — for diagnostics/monitoring and tests. */
export function getInjectQueueStats(): {
  lanes: number;
  active: number;
  waiting: number;
  pending: number;
  dropped: number;
  maxActiveLanes: number;
  maxQueuePerKey: number;
} {
  let pending = 0;
  for (const lane of lanes.values()) pending += lane.items.length;
  return {
    lanes: lanes.size,
    active: activeLanes,
    waiting: waitingKeys.length,
    pending,
    dropped: droppedTotal,
    maxActiveLanes: MAX_ACTIVE_LANES,
    maxQueuePerKey: MAX_QUEUE_PER_KEY,
  };
}

/**
 * Snapshot of every lane key enqueued since the last {@link resetInjectQueue}.
 * Read-only — for diagnostics/tests asserting per-flow lane unification.
 */
export function getEnqueuedLaneKeys(): string[] {
  return Array.from(enqueuedLaneKeys);
}

/** Clear all lanes and counters — for test isolation only. */
export function resetInjectQueue(): void {
  lanes.clear();
  waitingKeys.length = 0;
  activeLanes = 0;
  droppedTotal = 0;
  enqueuedLaneKeys.clear();
  dropNotifier = null;
}