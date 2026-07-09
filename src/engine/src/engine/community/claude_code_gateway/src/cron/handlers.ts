// RPC handlers for cron.* methods.
//
// Each handler is a pure function of (store, scheduler, params). The caller
// (server.ts handleFrame) flattens the returned result into a response frame:
//
//   const r = handleCronList(store, sched, params);
//   ctx.response(id, r.ok, r.ok ? r.payload : undefined, r.ok ? undefined : r.error);
//
// This keeps the dispatch table in server.ts short and makes unit tests easy.

import { randomUUID } from 'node:crypto';
import cronParser from 'cron-parser';
import { computeNextRunAtMs } from './scheduler.js';
import type { CronScheduler } from './scheduler.js';
import type { CronStore } from './store.js';
import type {
  CronAddRequest,
  CronJobRecord,
  CronListRequest,
  CronPayload,
  CronRunRequest,
  CronRunsRequest,
  CronSchedule,
  CronStatusPayload,
  CronUpdateRequest,
} from './types.js';

export type CronError = { code: string; message: string };
export type CronResult<T = unknown> =
  | { ok: true; payload: T }
  | { ok: false; error: CronError };

function err(code: string, message: string): CronResult<never> {
  return { ok: false, error: { code, message } };
}
function ok<T>(payload: T): CronResult<T> { return { ok: true, payload }; }

function asObject(value: unknown): Record<string, unknown> {
  return (value && typeof value === 'object' && !Array.isArray(value))
    ? value as Record<string, unknown>
    : {};
}

// ---- Validation ----

function validateSchedule(s: unknown): CronSchedule | CronError {
  const obj = asObject(s);
  const kind = obj.kind;
  if (kind === 'once') {
    const atMs = Number(obj.atMs);
    if (!Number.isFinite(atMs)) return { code: 'INVALID_SCHEDULE', message: 'once schedule requires numeric atMs' };
    return { kind: 'once', atMs, tz: typeof obj.tz === 'string' ? obj.tz : undefined };
  }
  if (kind === 'every') {
    const everyMs = Number(obj.everyMs);
    if (!Number.isFinite(everyMs) || everyMs <= 0) {
      return { code: 'INVALID_SCHEDULE', message: 'every schedule requires positive everyMs' };
    }
    const firstRunAtMs = Number(obj.firstRunAtMs);
    return {
      kind: 'every',
      everyMs,
      firstRunAtMs: Number.isFinite(firstRunAtMs) ? firstRunAtMs : undefined,
      tz: typeof obj.tz === 'string' ? obj.tz : undefined,
    };
  }
  if (kind === 'cron') {
    const expr = typeof obj.expr === 'string' ? obj.expr.trim() : '';
    if (!expr) return { code: 'INVALID_SCHEDULE', message: 'cron schedule requires expr string' };
    const tz = typeof obj.tz === 'string' ? obj.tz : undefined;
    try {
      cronParser.parseExpression(expr, { tz });
    } catch (e) {
      return { code: 'INVALID_SCHEDULE', message: `invalid cron expr: ${(e as Error).message}` };
    }
    return { kind: 'cron', expr, tz };
  }
  return { code: 'INVALID_SCHEDULE', message: `unknown schedule.kind: ${String(kind)}` };
}

function isCronError(x: unknown): x is CronError {
  return !!x && typeof x === 'object' && 'code' in (x as Record<string, unknown>)
    && 'message' in (x as Record<string, unknown>)
    && !('kind' in (x as Record<string, unknown>));
}

function validatePayload(p: unknown): CronPayload | CronError {
  const obj = asObject(p);
  const kind = typeof obj.kind === 'string' ? obj.kind : '';
  if (!kind) return { code: 'INVALID_PAYLOAD', message: 'payload.kind is required' };
  if (kind === 'agentTurn') {
    const message = typeof obj.message === 'string' ? obj.message : '';
    if (!message) return { code: 'INVALID_PAYLOAD', message: 'agentTurn payload requires message' };
    const out: CronPayload = { kind: 'agentTurn', message };
    if (typeof obj.model === 'string') (out as Record<string, unknown>).model = obj.model;
    if (typeof obj.permissionMode === 'string') (out as Record<string, unknown>).permissionMode = obj.permissionMode;
    else if (typeof obj.mode === 'string') (out as Record<string, unknown>).permissionMode = obj.mode;
    const t = Number(obj.timeoutSeconds);
    if (Number.isFinite(t) && t > 0) (out as Record<string, unknown>).timeoutSeconds = t;
    return out;
  }
  // Opaque payload — persisted but not fired.
  return { ...(obj as Record<string, unknown>), kind } as CronPayload;
}

// ---- Helpers ----

function enrichJobWithNextRun(job: CronJobRecord): CronJobRecord {
  const now = Date.now();
  const nextRunAtMs = job.enabled ? (computeNextRunAtMs(job.schedule, job, now) ?? null) : null;
  return { ...job, state: { ...job.state, nextRunAtMs } };
}

// ---- Handlers ----

export function handleCronList(
  store: CronStore,
  _scheduler: CronScheduler,
  params: unknown,
): CronResult<Array<CronJobRecord>> {
  const p = asObject(params);
  const includeDisabled = (p as CronListRequest).includeDisabled !== false;
  const jobs = store.list().filter(j => includeDisabled || j.enabled);
  return ok(jobs.map(enrichJobWithNextRun));
}

export function handleCronGet(
  store: CronStore,
  _scheduler: CronScheduler,
  params: unknown,
): CronResult<(CronJobRecord) | null> {
  const p = asObject(params);
  const id = typeof p.jobId === 'string' ? p.jobId : (typeof p.id === 'string' ? p.id : '');
  if (!id) return err('INVALID_ARGUMENT', 'jobId (or id) is required');
  const job = store.get(id);
  return ok(job ? enrichJobWithNextRun(job) : null);
}

export function handleCronStatus(
  store: CronStore,
  scheduler: CronScheduler,
  _params: unknown,
): CronResult<CronStatusPayload> {
  // `cron.status` takes no params today — reserved for future filters
  // (e.g. running window). Referenced to satisfy
  // `@typescript-eslint/no-unused-vars` which runs with `args: 'after-used'`
  // and no `argsIgnorePattern`, so trailing `_`-prefixed args still fail.
  void _params;
  const jobs = store.list();
  const enabled = jobs.filter(j => j.enabled);
  return ok({
    running: scheduler.runningJobIds().length > 0,
    jobCount: jobs.length,
    enabledCount: enabled.length,
    nextRunAtMs: scheduler.nextRunAtMs(),
  });
}

export function handleCronAdd(
  store: CronStore,
  _scheduler: CronScheduler,
  params: unknown,
): CronResult<CronJobRecord> {
  const p = asObject(params) as Partial<CronAddRequest> & Record<string, unknown>;
  const name = typeof p.name === 'string' ? p.name.trim() : '';
  if (!name) return err('INVALID_ARGUMENT', 'name is required');

  const schedule = validateSchedule(p.schedule);
  if (isCronError(schedule)) return { ok: false, error: schedule };

  const payload = validatePayload(p.payload);
  if (isCronError(payload)) return { ok: false, error: payload };

  const now = Date.now();
  const job: CronJobRecord = {
    id: randomUUID(),
    name,
    enabled: p.enabled !== false,
    schedule,
    payload,
    sessionTarget: typeof p.sessionTarget === 'string' ? p.sessionTarget : 'isolated',
    delivery: (p.delivery && typeof p.delivery === 'object')
      ? p.delivery as CronJobRecord['delivery']
      : undefined,
    state: {},
    createdAtMs: now,
    updatedAtMs: now,
  };
  store.put(job);
  return ok(job);
}

export function handleCronUpdate(
  store: CronStore,
  _scheduler: CronScheduler,
  params: unknown,
): CronResult<CronJobRecord> {
  const p = asObject(params) as Partial<CronUpdateRequest> & Record<string, unknown>;
  const id = typeof p.id === 'string' ? p.id : '';
  if (!id) return err('INVALID_ARGUMENT', 'id is required');
  const existing = store.get(id);
  if (!existing) return err('NOT_FOUND', `job ${id} not found`);

  const patch = asObject(p.patch);
  const next: CronJobRecord = { ...existing };

  if ('name' in patch) {
    const name = typeof patch.name === 'string' ? patch.name.trim() : '';
    if (!name) return err('INVALID_ARGUMENT', 'name must be non-empty when provided');
    next.name = name;
  }
  if ('schedule' in patch) {
    const s = validateSchedule(patch.schedule);
    if (isCronError(s)) return { ok: false, error: s };
    next.schedule = s;
    // Changing the schedule resets fire bookkeeping so `once` can fire again.
    next.state = { ...next.state, last_fired_at_ms: undefined };
  }
  if ('payload' in patch) {
    const pl = validatePayload(patch.payload);
    if (isCronError(pl)) return { ok: false, error: pl };
    next.payload = pl;
  }
  if ('enabled' in patch) next.enabled = !!patch.enabled;
  if ('sessionTarget' in patch && typeof patch.sessionTarget === 'string') {
    next.sessionTarget = patch.sessionTarget;
  }
  if ('delivery' in patch) {
    next.delivery = patch.delivery
      ? patch.delivery as CronJobRecord['delivery']
      : undefined;
  }

  next.updatedAtMs = Date.now();
  store.put(next);
  return ok(next);
}

export function handleCronRemove(
  store: CronStore,
  _scheduler: CronScheduler,
  params: unknown,
): CronResult<{ removed: boolean }> {
  const p = asObject(params);
  const id = typeof p.id === 'string' ? p.id : '';
  if (!id) return err('INVALID_ARGUMENT', 'id is required');
  const removed = store.delete(id);
  return ok({ removed });
}

export function handleCronRun(
  store: CronStore,
  scheduler: CronScheduler,
  params: unknown,
): CronResult<{ ran: string; runId?: string; status: string }> {
  const p = asObject(params) as Partial<CronRunRequest> & Record<string, unknown>;
  const id = typeof p.id === 'string' ? p.id : '';
  if (!id) return err('INVALID_ARGUMENT', 'id is required');
  const job = store.get(id);
  if (!job) return err('NOT_FOUND', `job ${id} not found`);
  if (scheduler.runningJobIds().includes(id)) {
    return ok({ ran: id, status: 'already_running' });
  }
  const mode = p.mode === 'due' ? 'due' : 'force';
  if (mode === 'due') {
    const next = computeNextRunAtMs(job.schedule, job, Date.now());
    if (next == null || next > Date.now()) {
      return ok({ ran: id, status: 'not_due' });
    }
  }
  // Fire-and-forget: dispatch asynchronously, return immediately.
  const runId = scheduler.fireAsync(id);
  return ok({ ran: id, runId: runId ?? undefined, status: runId ? 'dispatched' : 'skipped' });
}

export function handleCronRuns(
  store: CronStore,
  _scheduler: CronScheduler,
  params: unknown,
): CronResult<{ entries: ReturnType<CronStore['getRuns']> }> {
  const p = asObject(params) as Partial<CronRunsRequest> & Record<string, unknown>;
  const id = typeof p.id === 'string' ? p.id : '';
  if (!id) return err('INVALID_ARGUMENT', 'id is required');
  const limit = Number.isFinite(Number(p.limit)) ? Math.max(1, Number(p.limit)) : 20;
  return ok({ entries: store.getRuns(id, limit) });
}

// ---- Dispatch table (used by server.ts) ----

export type CronHandler = (
  store: CronStore,
  scheduler: CronScheduler,
  params: unknown,
) => CronResult | Promise<CronResult>;

export const CRON_METHODS: Record<string, CronHandler> = {
  'cron.list': handleCronList,
  'cron.get': handleCronGet,
  'cron.status': handleCronStatus,
  'cron.add': handleCronAdd,
  'cron.update': handleCronUpdate,
  'cron.remove': handleCronRemove,
  'cron.run': handleCronRun,
  'cron.runs': handleCronRuns,
};
