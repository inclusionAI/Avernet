import type { OverviewDays, OverviewRunFilters } from './overviewTypes';

/** Server run/node numeric timestamps are Unix seconds. Accept explicit ISO and legacy ms too. */
export function parseOverviewTimestamp(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const number = Number(value);
  const ms = Number.isFinite(number)
    ? Math.abs(number) < 1e11
      ? number * 1000
      : number
    : typeof value === 'string'
    ? Date.parse(value)
    : NaN;
  return Number.isFinite(ms) && Math.abs(ms) <= 8.64e15 ? ms : null;
}
export function overviewTimeRange(days: OverviewDays, now = Date.now()): { from: number; to: number } {
  const date = new Date(now);
  date.setHours(0, 0, 0, 0);
  const today = Math.floor(date.getTime() / 1000),
    end = Math.floor(now / 1000);
  if (days === 'today') return { from: today, to: end };
  if (days === 'yesterday') {
    const previous = new Date(date);
    previous.setDate(previous.getDate() - 1);
    return { from: Math.floor(previous.getTime() / 1000), to: today - 1 };
  }
  return { from: end - Number(days) * 86400, to: end };
}
export function runListParams(
  workflowId: string,
  userId: string | undefined,
  filters: OverviewRunFilters,
  now = Date.now(),
) {
  const to = Math.floor(now / 1000);
  const days: Record<string, number> = { '1d': 1, '3d': 3, '7d': 7 };
  return {
    workflowId,
    user_id: userId,
    limit: 20,
    offset: filters.page * 20,
    status: filters.status && filters.status !== 'cancelled' ? filters.status : undefined,
    statuses: filters.status === 'cancelled' ? 'cancelled,canceled' : undefined,
    query: filters.query.trim() || undefined,
    from: days[filters.time] ? to - days[filters.time] * 86400 : undefined,
    to: days[filters.time] ? to : undefined,
  };
}
export function overviewStatistics(counts: Record<string, number> | null) {
  if (counts === null) return null;
  const succeeded = counts.succeeded ?? 0;
  const abnormal = ['failed', 'aborted', 'cancelled', 'canceled'].reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  const terminal = succeeded + abnormal;
  return {
    succeeded,
    abnormal,
    terminal,
    total: Object.values(counts).reduce((sum, count) => sum + count, 0),
    successRate: terminal ? (succeeded / terminal) * 100 : null,
    running: counts.running ?? 0,
    waiting: counts.waiting ?? 0,
    blocked: counts.blocked ?? 0,
    queued: counts.queued ?? 0,
  };
}
export function overviewDuration(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}min`;
}
export function overviewTime(ms: number | null | undefined) {
  return ms === null || ms === undefined ? '—' : new Date(ms).toLocaleString('zh-CN', { hour12: false });
}
export const overviewStatusLabels: Record<string, string> = {
  succeeded: '成功',
  failed: '失败',
  running: '运行中',
  waiting: '等待中',
  blocked: '阻塞',
  queued: '排队中',
  cancelled: '已取消',
  canceled: '已取消',
  aborted: '已终止',
  pending: '待执行',
  postActionsRunning: '后处理运行中',
  skipped: '已跳过',
  unknown: '未知',
};
export const overviewStatuses = [
  'running',
  'succeeded',
  'failed',
  'waiting',
  'blocked',
  'queued',
  'cancelled',
  'aborted',
];
export function overviewListQuery(filters: OverviewRunFilters, searchInput: string) {
  return {
    runPage: String(filters.page),
    runStatus: filters.status || undefined,
    runQuery: filters.query || undefined,
    runSearch: searchInput || undefined,
    runRange: filters.time,
  };
}
export function readOverviewFilters(query: Record<string, string | undefined>, saved?: string | null) {
  let stored: Record<string, unknown> = {};
  try {
    const value: unknown = JSON.parse(saved || '{}');
    if (value && typeof value === 'object') stored = value as Record<string, unknown>;
  } catch {
    /* Optional storage only. */
  }
  const read = (key: string) => query[key] ?? stored[key];
  const page = Number(read('runPage') ?? 0),
    status = read('runStatus'),
    time = query.runRange ?? query.runTime ?? stored.runRange ?? stored.runTime;
  return {
    filters: {
      page: Number.isSafeInteger(page) && page >= 0 ? page : 0,
      status: typeof status === 'string' && overviewStatuses.includes(status) ? status : '',
      query: typeof read('runQuery') === 'string' ? String(read('runQuery')) : '',
      time: ['', '1d', '3d', '7d'].includes(String(time)) ? String(time) : '7d',
    },
    search:
      typeof read('runSearch') === 'string'
        ? String(read('runSearch'))
        : typeof read('runQuery') === 'string'
        ? String(read('runQuery'))
        : '',
  };
}
