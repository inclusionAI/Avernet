import { MonitoringError, type MonitoringWindow } from './contracts.js';
import { parseQuery } from './validation.js';

export function queryKeys(query: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(query).some(k => !keys.includes(k) || typeof query[k] !== 'string')) {
    throw new MonitoringError('INVALID_EVENT', '不支持此查询参数。');
  }
}
export function parseWindow(query: Record<string, unknown>, now: number): MonitoringWindow {
  if (query.start === undefined && query.end === undefined) {
    const day = Math.floor((now + 8 * 3600_000) / 86400_000) * 86400_000 - 8 * 3600_000;
    return { startMs: day - 6 * 86400_000, endMs: day + 86400_000 };
  }
  const bound = (v: unknown) => {
    if (v === 'all') return null;
    if (typeof v !== 'string' || !/^\d{1,16}$/.test(v) || !Number.isSafeInteger(Number(v)) || Number(v) > 8640000000000000) {
      throw new MonitoringError('INVALID_EVENT', '时间范围无效。');
    }
    return Number(v);
  };
  const startMs = bound(query.start), endMs = bound(query.end);
  if (startMs !== null && endMs !== null && (startMs >= endMs || endMs - startMs > 366 * 86400_000)) {
    throw new MonitoringError('INVALID_EVENT', '时间范围须递增且不超过 366 天。');
  }
  return { startMs, endMs };
}
export function browserDiagnosisQuery(query: Record<string, unknown>, now: number) {
  queryKeys(query, ['start', 'end', 'page', 'pageSize', 'decision', 'keyword', 'businessProblemCategory', 'businessProblemSubtype']);
  const { start: _start, end: _end, ...rest } = query;
  return { ...parseQuery(rest), ...parseWindow(query, now) };
}
