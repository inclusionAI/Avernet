/** Wire contract shared with ClawMind src/aistudio/types.ts. All business times are epoch seconds. */
export type WatchRecord = {
  watchId: string;
  flowId: string;
  workflowId: string;
  nodeId: string;
  attempt: number;
  ownerBotId: string;
  mode: 'submit' | 'watch';
  recordId: string;
  detailUrl: string | null;
  phase: 'waiting' | 'polling' | 'resolving' | 'resolved';
  lastStatus: string | null;
  submittedAt: number;
  firstPollAt: number;
  nextPollAt: number;
  timeoutAt: number;
  pollIntervalSeconds: number;
  lastPolledAt: number | null;
  lastPollSource: 'auto' | 'manual' | null;
  errorCount: number;
  lastError: string | null;
  leaseOwner: string | null;
  leaseExpiresAt: number | null;
  resolutionStatus: 'success' | 'failed' | 'timeout' | null;
  resolutionStartedAt: number | null;
  resolvedAt: number | null;
  canceled: boolean;
  manualRequestedAt: number | null;
  manualRequestedBy: string | null;
  lastManualRequestedAt: number | null;
  version: number;
};

export type WatchDueCursor = { manualPriority: 0 | 1; nextPollAt: number; watchId: string };

export interface AIStudioTrainWatchStore {
  create(record: WatchRecord): Promise<WatchRecord>;
  find(flowId: string, nodeId: string, attempt?: number): Promise<WatchRecord | null>;
  findDue(ownerBotId: string, now: number, limit: number, after?: WatchDueCursor): Promise<WatchRecord[]>;
  compareAndSwap(expectedVersion: number, next: WatchRecord, leaseOwner?: string): Promise<boolean>;
}
/** Identity is immutable even when a caller submits a forged CAS payload. */
export const WATCH_IDENTITY_FIELDS = [
  'watchId', 'flowId', 'workflowId', 'nodeId', 'attempt', 'ownerBotId', 'mode', 'recordId',
] as const satisfies readonly (keyof WatchRecord)[];
export const WATCH_FIELDS = [
  ...WATCH_IDENTITY_FIELDS, 'detailUrl', 'phase', 'lastStatus', 'submittedAt', 'firstPollAt',
  'nextPollAt', 'timeoutAt', 'pollIntervalSeconds', 'lastPolledAt', 'lastPollSource',
  'errorCount', 'lastError', 'leaseOwner', 'leaseExpiresAt', 'resolutionStatus',
  'resolutionStartedAt', 'resolvedAt', 'canceled', 'manualRequestedAt', 'manualRequestedBy',
  'lastManualRequestedAt', 'version',
] as const satisfies readonly (keyof WatchRecord)[];

export const MANUAL_POLL_COOLDOWN_SECONDS = 60;
const epochMax = 253402300799; // ISO projection remains representable; reject milliseconds.
export function isEpochSeconds(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0 && (value as number) <= epochMax;
}
export function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 1;
}
export function isIdentity(value: unknown, max = 255): value is string {
  return typeof value === 'string' && value.length <= max && /^[A-Za-z0-9_][A-Za-z0-9_.:-]*$/.test(value);
}
export function isOwnerBotId(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 255 && /^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$/.test(value);
}
/** Link-only public HTTPS URLs: no private hosts, credentials or token-bearing queries. Never fetched. */
export function safeDetailUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 1024 || /[\s\\\x00-\x1f\x7f]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || (url.port && url.port !== '443')
      || !/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(url.hostname)
      || /(^|\.)(localhost|local|internal|intranet|test|invalid)$/i.test(url.hostname)
      || [...url.searchParams.keys()].some(key => /token|secret|password|credential|signature|key/i.test(key))) return null;
    return value;
  } catch { return null; }
}
export function assertWatchRecord(value: unknown): asserts value is WatchRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid AIStudio watch');
  const w = value as Record<string, unknown>;
  if (Object.keys(w).length !== WATCH_FIELDS.length || Object.keys(w).some(k => !(WATCH_FIELDS as readonly string[]).includes(k))) {
    throw new Error('Invalid AIStudio watch fields');
  }
  for (const key of ['watchId', 'flowId', 'workflowId', 'nodeId']) {
    if (!isIdentity(w[key], key === 'watchId' ? 64 : 255)) throw new Error('Invalid AIStudio identity');
  }
  if (!isOwnerBotId(w.ownerBotId) || typeof w.recordId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$/.test(w.recordId)
    || !['submit', 'watch'].includes(w.mode as string) || !['waiting','polling','resolving','resolved'].includes(w.phase as string)
    || typeof w.canceled !== 'boolean') throw new Error('Invalid AIStudio watch fields');
  for (const key of ['submittedAt','firstPollAt','nextPollAt','timeoutAt']) {
    if (!isEpochSeconds(w[key])) throw new Error('Invalid AIStudio epoch seconds');
  }
  for (const key of ['attempt','pollIntervalSeconds','version']) {
    if (!isPositiveInteger(w[key])) throw new Error('Invalid AIStudio positive integer');
  }
  if (!Number.isSafeInteger(w.errorCount) || (w.errorCount as number) < 0) throw new Error('Invalid error count');
  for (const key of ['lastPolledAt','leaseExpiresAt','resolutionStartedAt','resolvedAt','manualRequestedAt','lastManualRequestedAt']) {
    if (w[key] !== null && !isEpochSeconds(w[key])) throw new Error('Invalid AIStudio epoch seconds');
  }
  for (const key of ['detailUrl','lastStatus','lastError','leaseOwner','manualRequestedBy']) {
    if (w[key] !== null && (typeof w[key] !== 'string' || (w[key] as string).length > (key === 'lastError' ? 4000 : key === 'detailUrl' ? 1024 : 255))) {
      throw new Error('Invalid AIStudio nullable text');
    }
  }
  if (w.detailUrl !== null && safeDetailUrl(w.detailUrl) === null) throw new Error('Invalid AIStudio detail URL');
  if (![null,'auto','manual'].includes(w.lastPollSource as null) || ![null,'success','failed','timeout'].includes(w.resolutionStatus as null)) {
    throw new Error('Invalid AIStudio watch status');
  }
}
export function assertWatchCas(expectedVersion: number, next: WatchRecord): void {
  assertWatchRecord(next);
  if (!isPositiveInteger(expectedVersion) || next.version !== expectedVersion + 1) throw new Error('AIStudio CAS requires next.version = expectedVersion + 1');
}
/** Version fences persisted state, not now-derived manualPollAllowed/manualPollRetryAt projections. */
export type AIStudioTrainWatchDto = Pick<WatchRecord, 'watchId'|'flowId'|'nodeId'|'attempt'|'mode'|'recordId'|'phase'|'lastStatus'|'lastPollSource'|'errorCount'|'version'> & {
  detailUrl: string | null; submittedAt: string; firstPollAt: string; nextPollAt: string | null;
  timeoutAt: string; pollInterval: number; lastPolledAt: string | null; lastError: string | null;
  manualPollAllowed: boolean; manualPollRetryAt: string | null; manualPollPending: boolean;
};
const publicStatuses = new Set(['submitted','accepted','running','prepare','0','1','2','6','11','success','completed','3',
  'failed','error','prepare fail','4','7','stopped','cancelled','canceled','deleted','unknown','timeout','waiting']);
export function toWatchDto(w: WatchRecord, now: number): AIStudioTrainWatchDto {
  const active = !w.canceled && ['waiting','polling'].includes(w.phase) && now < w.timeoutAt;
  const retryAt = w.lastManualRequestedAt === null ? null : w.lastManualRequestedAt + MANUAL_POLL_COOLDOWN_SECONDS;
  const iso = (v: number) => new Date(v * 1000).toISOString();
  return {
    watchId: w.watchId, version: w.version, flowId: w.flowId, nodeId: w.nodeId, attempt: w.attempt, mode: w.mode,
    recordId: w.recordId, detailUrl: safeDetailUrl(w.detailUrl), phase: w.phase,
    lastStatus: w.lastStatus === null ? null : publicStatuses.has(w.lastStatus.trim().toLowerCase()) ? w.lastStatus : 'unknown',
    submittedAt: iso(w.submittedAt), firstPollAt: iso(w.firstPollAt), nextPollAt: w.phase === 'resolved' || w.canceled ? null : iso(w.nextPollAt),
    timeoutAt: iso(w.timeoutAt), pollInterval: w.pollIntervalSeconds, lastPolledAt: w.lastPolledAt === null ? null : iso(w.lastPolledAt),
    lastPollSource: w.lastPollSource, errorCount: w.errorCount,
    // Translate only the exact watcher diagnostic; never forward raw error text.
    lastError: w.lastError === null ? null : w.lastError === 'AIStudio status unknown'
      ? 'AIStudio returned an unrecognized status' : 'AIStudio status query failed',
    manualPollAllowed: active && w.manualRequestedAt === null && (retryAt === null || retryAt <= now),
    manualPollRetryAt: retryAt !== null && retryAt > now ? iso(retryAt) : null,
    manualPollPending: w.manualRequestedAt !== null,
  };
}

export function assertWatchLeaseOwner(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !value.trim() || value.length > 255) throw new Error('Invalid AIStudio lease owner');
}
export function assertWatchDueCursor(value: unknown): asserts value is WatchDueCursor {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid AIStudio due cursor');
  const cursor = value as Record<string, unknown>;
  if (Object.keys(cursor).length !== 3 || ![0,1].includes(cursor.manualPriority as number)
    || !isEpochSeconds(cursor.nextPollAt) || !isIdentity(cursor.watchId,64)) throw new Error('Invalid AIStudio due cursor');
}
