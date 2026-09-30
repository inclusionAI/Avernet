import { fetchJson } from '@avernet/clawweb-shared/web/api/client';
import type { BotStatus, DiagnosisPage, MonitoringQuery, BotOption, BotOptionsPage, BotScope } from '../types/monitoring';

const BASE = '/api/insight/v1/monitoring';
/** Reuse shared login context; monitoring does not introduce a separate browser credential. */
export function monitoringWindow(query: Pick<MonitoringQuery, 'startDate' | 'endDate'>): URLSearchParams {
  return new URLSearchParams({
    start: query.startDate ? String(Date.parse(`${query.startDate}T00:00:00+08:00`)) : 'all',
    end: query.endDate ? String(Date.parse(`${query.endDate}T00:00:00+08:00`) + 86400000) : 'all',
  });
}
export const monitoringApi = {
  options: (scope: BotScope, q: string, dates: Pick<MonitoringQuery, 'startDate' | 'endDate'>, cursor: string | null, signal?: AbortSignal) => {
    const params = monitoringWindow(dates);
    params.set('scope', scope); params.set('q', q); params.set('limit', '20');
    if (cursor) params.set('cursor', cursor);
    return fetchJson<BotOptionsPage>(`${BASE}/bot-options?${params}`, { signal });
  },
  targetStatus: (ref: string, dates: Pick<MonitoringQuery, 'startDate' | 'endDate'>, signal?: AbortSignal) =>
    fetchJson<BotOption>(`${BASE}/targets/${encodeURIComponent(ref)}/status?${monitoringWindow(dates)}`, { signal }),
  targetDiagnoses: (ref: string, query: MonitoringQuery, signal?: AbortSignal) => {
    const params = monitoringWindow(query);
    Object.entries(query).forEach(([key, value]) => {
      if (key !== 'startDate' && key !== 'endDate' && value !== '') params.set(key, String(value));
    });
    return fetchJson<DiagnosisPage>(`${BASE}/targets/${encodeURIComponent(ref)}/diagnoses?${params}`, { signal });
  },
  enroll: (botRef: string, signal?: AbortSignal) => fetchJson<never>(`${BASE}/enrollments`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ botRef }), signal,
  }),
  bots: (signal?: AbortSignal) => fetchJson<{ items: { botId: string }[] }>(`${BASE}/bots`, { signal }),
  status: (botId: string, signal?: AbortSignal) =>
    fetchJson<BotStatus>(`${BASE}/bots/${encodeURIComponent(botId)}/status`, { signal }),
  diagnoses: (botId: string, query: MonitoringQuery, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value !== '') params.set(key, String(value)); });
    return fetchJson<DiagnosisPage>(`${BASE}/bots/${encodeURIComponent(botId)}/diagnoses?${params}`, { signal });
  },
};

export function monitoringErrorText(error: unknown): string {
  const status = (error as { status?: number } | null)?.status;
  if (status === 401) return '登录状态已失效，请重新登录后刷新。';
  if (status === 403) return '暂时无法访问监控数据，请联系管理员。';
  if (status === 404) return '该 Bot 不存在或已无权访问，请重新选择。';
  if (status === 400) return '查询条件无效，请检查日期和搜索内容。';
  if (status === 503) return '监控服务暂不可用，请稍后重试。';
  if (status === 504 || (error as { name?: string } | null)?.name === 'TimeoutError') return '请求超时，请重试。';
  return '监控数据加载失败，请检查网络后重试。';
}

/** The placeholder dialog is shown only for the endpoint's documented code, not arbitrary 501s. */
export function monitoringErrorCode(error: unknown): string | undefined {
  const body = (error as { body?: string } | null)?.body;
  try {
    const payload = JSON.parse(body ?? 'null');
    return payload?.error?.code ?? payload?.code;
  } catch { return undefined; }
}
