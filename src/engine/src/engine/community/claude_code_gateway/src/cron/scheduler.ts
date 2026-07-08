import cronParser from 'cron-parser';
import { createLogger } from '../debug.js';
import {
  startChatRun,
  type OrchestratorFinal,
  type RunningOrchestration,
} from '../chat-orchestrator.js';
import { createCronExecutionId, createManualRunId } from './run-id.js';
import type { CronStore } from './store.js';
import type {
  CronJobRecord,
  CronPayloadAgentTurn,
  CronRunRecord,
  CronSchedule,
} from './types.js';

const log = createLogger('cron');

const DEFAULT_TICK_MS = 1000;
const DEFAULT_TIMEOUT_SECS = 24 * 3600;
const DEFAULT_SUMMARY_MAX = 16 * 1024 * 1024;

export type ChatRunStarter = (
  params: {
    cwd: string;
    message: string;
    model?: string;
    permissionMode?: string;
  },
) => RunningOrchestration;

export type SchedulerOptions = {
  /** Tick cadence in ms. Defaults to 1000. Tests set this to a small value. */
  tickIntervalMs?: number;
  /** CWD handed to the chat runner on fire. Defaults to `process.cwd()`. */
  cwd?: string;
  /** Swap out the chat runner (tests). Defaults to a thin `startChatRun` wrap. */
  chatRunner?: ChatRunStarter;
  /** Wall-clock source (tests inject a fake). */
  now?: () => number;
  /** Per-run summary truncation. */
  summaryMaxChars?: number;
};

/**
 * Tick-driven cron scheduler. Lifecycle:
 *
 *   const sched = new CronScheduler(store, options);
 *   sched.start();       // idempotent; starts the tick loop
 *   ...
 *   await sched.stop();  // cancels the tick + aborts in-flight jobs
 *
 * Firing rules:
 *   * `once` jobs disable themselves after the first fire (ok, error, or
 *     skipped); `every` / `cron` jobs re-arm by computing the next fire.
 *   * A job already running (`state.running_at_ms` set) is not fired again
 *     until the running run completes — this avoids pile-ups when a fire
 *     takes longer than the tick interval.
 *   * `runJobNow(id, mode)` ignores the due check when `mode === 'force'`.
 *
 * Wire format: all `*AtMs` fields are epoch-ms; `schedule.kind` is the
 * openclaw-parity enum (`once` / `every` / `cron`); payload.kind is usually
 * `agentTurn`. Non-`agentTurn` payloads record a `skipped` run with an error.
 */
export class CronScheduler {
  private readonly store: CronStore;
  private readonly tickIntervalMs: number;
  private readonly cwd: string;
  private readonly chatRunner: ChatRunStarter;
  private readonly now: () => number;
  private readonly summaryMaxChars: number;

  private timer: NodeJS.Timeout | null = null;
  private readonly running = new Map<string, RunningOrchestration>();
  // Process-wide monotonic counter for manual (cron.run / runJobNow) fires —
  // mirrors openclaw's module-level nextManualRunId. Not persisted; resets to 1
  // on restart, same as openclaw.
  private nextManualRunId = 1;

  constructor(store: CronStore, options: SchedulerOptions = {}) {
    this.store = store;
    this.tickIntervalMs = options.tickIntervalMs ?? DEFAULT_TICK_MS;
    this.cwd = options.cwd ?? process.cwd();
    this.chatRunner = options.chatRunner ?? defaultChatRunner;
    this.now = options.now ?? (() => Date.now());
    this.summaryMaxChars = options.summaryMaxChars ?? DEFAULT_SUMMARY_MAX;
  }

  /** Start the tick loop. Idempotent. */
  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => { void this.tick(); }, this.tickIntervalMs);
    // Don't hold the event loop open for the tick alone.
    this.timer.unref?.();
    log.debug('start', { tickIntervalMs: this.tickIntervalMs, cwd: this.cwd });
  }

  /** Stop the tick loop and abort any in-flight runs. Awaits completion. */
  async stop(): Promise<void> {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    const inflight = [ ...this.running.values() ];
    for (const r of inflight) {
      try { r.abort(); } catch { /* ignore */ }
    }
    await Promise.allSettled(inflight.map(r => r.completed));
    this.running.clear();
  }

  /** Snapshot of currently-running job ids (used by cron.status). */
  runningJobIds(): string[] {
    return [ ...this.running.keys() ];
  }

  /**
   * Compute the earliest upcoming fire time across enabled jobs, or null.
   * Used by `cron.status` — skips jobs that are currently running.
   */
  nextRunAtMs(): number | null {
    let best: number | null = null;
    const now = this.now();
    for (const job of this.store.list()) {
      if (!job.enabled) continue;
      if (this.running.has(job.id)) continue;
      const next = computeNextRunAtMs(job.schedule, job, now);
      if (next == null) continue;
      if (best == null || next < best) best = next;
    }
    return best;
  }

  // ---- Tick & fire ----

  private async tick(): Promise<void> {
    const now = this.now();
    // Snapshot first — firing can mutate the store, which would invalidate
    // an in-progress iterator.
    const jobs = this.store.list();
    for (const job of jobs) {
      if (!job.enabled) continue;
      if (this.running.has(job.id)) continue;
      const next = computeNextRunAtMs(job.schedule, job, now);
      if (next == null || next > now) continue;
      void this.fire(job, now, 'scheduled');
    }
  }

  /**
   * Force-run a job by id. Fires now regardless of schedule. Returns a
   * promise that resolves when the run completes.
   *
   * `mode === 'due'` respects the schedule (noop when not due) — same as a
   * natural tick fire. `mode === 'force'` ignores it.
   */
  async runJobNow(id: string, mode: 'due' | 'force' = 'force'): Promise<CronRunRecord | null> {
    const job = this.store.get(id);
    if (!job) return null;
    if (this.running.has(id)) return null; // already running
    const now = this.now();
    if (mode === 'due') {
      const next = computeNextRunAtMs(job.schedule, job, now);
      if (next == null || next > now) return null;
    }
    return this.fire(job, now, 'manual');
  }

  /**
   * Fire a job asynchronously (fire-and-forget). Returns the runId if
   * dispatched, or null if the job is already running / not found.
   * Used by `cron.run` RPC to return immediately without blocking.
   */
  fireAsync(id: string): string | null {
    const job = this.store.get(id);
    if (!job) return null;
    if (this.running.has(id)) return null;
    void this.fire(job, this.now(), 'manual');
    // fire() writes runId into state.last_run_id synchronously before await
    const updated = this.store.get(id);
    return (updated?.state?.last_run_id as string) ?? null;
  }

  private async fire(
    job: CronJobRecord,
    fireAtMs: number,
    source: 'scheduled' | 'manual',
  ): Promise<CronRunRecord> {
    // openclaw parity: scheduled (tick) → cron:<id>:<startedAt>; manual
    // (cron.run / runJobNow) → manual:<id>:<startedAt>:<attempt>. Generated
    // once per fire and reused for state, skipped, and normal run records.
    const runId =
      source === 'scheduled'
        ? createCronExecutionId(job.id, fireAtMs)
        : createManualRunId(job.id, fireAtMs, this.nextManualRunId++);
    this.store.updateState(job.id, { running_at_ms: fireAtMs, last_run_id: runId });
    log.debug('fire:start', { id: job.id, name: job.name, runId, source });

    const payload = job.payload as CronPayloadAgentTurn;
    const started = this.now();

    // Payload guard — the relay only knows how to run agentTurn. Others are
    // recorded as skipped so the history shows what happened.
    if (payload.kind !== 'agentTurn') {
      const finished = this.now();
      const rec: CronRunRecord = {
        jobId: job.id,
        runId,
        runAtMs: started,
        ts: finished,
        status: 'skipped',
        error: `payload.kind=${payload.kind} not supported by relay`,
        durationMs: finished - started,
      };
      this.store.appendRun(rec);
      this.finalizeJob(job, started, 'skipped', rec.durationMs);
      return rec;
    }

    const timeoutSecs = payload.timeoutSeconds ?? DEFAULT_TIMEOUT_SECS;
    const permissionMode = payload.permissionMode || 'bypassPermissions';
    log.warn('fire:chatRunner', {
      id: job.id, name: job.name, runId,
      model: payload.model, permissionMode,
      timeoutSecs, cwd: this.cwd,
      message: payload.message.substring(0, 80),
    });
    const running = this.chatRunner({
      cwd: this.cwd,
      message: payload.message,
      model: payload.model,
      permissionMode,
    });
    this.running.set(job.id, running);

    // Collect text so we can summarize.
    let finalText = '';
    running.subscribe(event => {
      if (event.kind === 'textDelta') finalText = event.fullText;
    });

    let result: OrchestratorFinal;
    try {
      result = await withTimeout(running.completed, timeoutSecs * 1000, () => {
        try { running.abort(); } catch { /* ignore */ }
      });
    } catch (err) {
      // withTimeout threw — treat as error.
      result = {
        ok: false,
        text: finalText,
        error: err instanceof Error ? err.message : String(err),
        toolUses: [],
        aborted: true,
      };
    } finally {
      this.running.delete(job.id);
    }

    const finished = this.now();
    const status: CronRunRecord['status'] = result.ok ? 'ok' : 'error';
    const summary = truncate(finalText || result.text || '', this.summaryMaxChars);
    const rec: CronRunRecord = {
      jobId: job.id,
      runId,
      runAtMs: started,
      ts: finished,
      status,
      error: result.error,
      durationMs: finished - started,
      summary: summary || undefined,
    };
    this.store.appendRun(rec);
    this.finalizeJob(job, started, status, rec.durationMs);
    log.warn('fire:end', {
      id: job.id, name: job.name, runId, status,
      durationMs: rec.durationMs,
      error: result.error ? result.error.substring(0, 150) : undefined,
      summary: summary ? summary.substring(0, 80) : undefined,
      aborted: result.aborted,
    });
    return rec;
  }

  private finalizeJob(
    job: CronJobRecord,
    firedAtMs: number,
    status: CronRunRecord['status'],
    durationMs?: number,
  ): void {
    // Clear running marker; update run bookkeeping to align with openclaw state shape.
    const prevState = job.state || {};
    const prevErrors = (typeof prevState.consecutive_errors === 'number' ? prevState.consecutive_errors : 0);
    const prevSkipped = (typeof prevState.consecutive_skipped === 'number' ? prevState.consecutive_skipped : 0);

    const patch: Record<string, unknown> = {
      running_at_ms: undefined,
      last_fired_at_ms: firedAtMs,
      last_run_at_ms: firedAtMs,
      last_status: status,
      last_run_status: status,
      last_duration_ms: durationMs ?? 0,
      last_delivery_status: 'not-requested',
      consecutive_errors: status === 'error' ? prevErrors + 1 : 0,
      consecutive_skipped: status === 'skipped' ? prevSkipped + 1 : 0,
    };
    this.store.updateState(job.id, patch);
    if (job.schedule.kind === 'once') {
      const current = this.store.get(job.id);
      if (current) {
        this.store.put({ ...current, enabled: false, updatedAtMs: this.now() });
      }
    }
  }
}

// ---- Next-run computation (exported for tests) ----

/**
 * Return the next fire time in epoch-ms for `schedule`, or null if the job
 * will never fire again. Uses `lastFiredAtMs` from `job.state` to advance
 * past already-fired `once` / `every` slots.
 *
 * `cron` schedules always consult `cron-parser`; the returned Date is local
 * to the given `tz` (or UTC).
 */
export function computeNextRunAtMs(
  schedule: CronSchedule,
  job: Pick<CronJobRecord, 'state'>,
  now: number,
): number | null {
  const lastFiredAtMs = typeof job.state.last_fired_at_ms === 'number'
    ? job.state.last_fired_at_ms
    : undefined;

  if (schedule.kind === 'once') {
    if (lastFiredAtMs != null) return null; // already fired
    return schedule.atMs;
  }

  if (schedule.kind === 'every') {
    const step = Math.max(1, schedule.everyMs);
    const first = schedule.firstRunAtMs ?? (now + step);
    if (lastFiredAtMs == null) {
      // First run: honor `firstRunAtMs` even if it's in the past (fire asap).
      return first;
    }
    // Advance in full `step` increments past `now` to avoid a burst after a
    // long outage.
    const base = Math.max(lastFiredAtMs, first);
    if (base + step > now) return base + step;
    const missed = Math.ceil((now - base) / step);
    return base + missed * step;
  }

  // cron — check whether the most recent slot was missed (tick landed a few ms
  // past the boundary). Use prev() to catch the slot the tick just crossed, but
  // only if it's recent (within 60s) — stale slots from hours/days ago (e.g. a
  // daily job's yesterday slot) must NOT be retroactively fired on startup.
  try {
    const interval = cronParser.parseExpression(schedule.expr, {
      tz: schedule.tz,
      currentDate: new Date(now),
    });
    const prev = interval.prev().getTime();
    const CATCHUP_WINDOW_MS = 60_000;
    const prevIsRecent = (now - prev) < CATCHUP_WINDOW_MS;
    if (prevIsRecent && (lastFiredAtMs == null || prev > lastFiredAtMs)) {
      return prev;
    }
    // Re-create interval since prev() consumed the iterator state.
    const fwd = cronParser.parseExpression(schedule.expr, {
      tz: schedule.tz,
      currentDate: new Date(now),
    });
    return fwd.next().getTime();
  } catch (err) {
    log.warn('cron-parse-failed', { expr: schedule.expr, error: (err as Error).message });
    return null;
  }
}

// ---- Helpers ----

function defaultChatRunner(params: {
  cwd: string;
  message: string;
  model?: string;
  permissionMode?: string;
}): RunningOrchestration {
  return startChatRun({
    cwd: params.cwd,
    message: params.message,
    model: params.model,
    permissionMode: params.permissionMode,
  });
}

function truncate(s: string, max: number): string {
  if (!s) return '';
  if (s.length <= max) return s;
  return `${s.slice(0, max)}…`;
}

/**
 * Race a promise against a wall-clock timeout. On timeout, invoke `onTimeout`
 * (used to abort the underlying run) and reject with a timeout error. The
 * underlying `promise` is still awaited so the caller gets the final state
 * (it should resolve shortly after abort).
 */
async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  onTimeout: () => void,
): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return promise;
  let timer: NodeJS.Timeout | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      onTimeout();
      reject(new Error(`cron fire timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    timer.unref?.();
  });
  try {
    return await Promise.race([ promise, timeout ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
