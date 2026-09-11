import { fetchJson } from '@avernet/clawweb-shared/web/api/client';
import type { BotStatus, DiagnosisPage, MonitoringQuery } from '../types/monitoring';

const BASE = '/api/insight/v1/monitoring';
/** Reuse shared login context; monitoring does not introduce a separate browser credential. */
export const monitoringApi = {
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
  if (status === 404) return '该 Bot 已不在监控清单中，请重新加载 Bot 列表。';
  if (status === 400) return '查询条件无效，请检查日期和搜索内容。';
  if (status === 503) return '监控服务暂不可用，请稍后重试。';
  if (status === 504 || (error as { name?: string } | null)?.name === 'TimeoutError') return '请求超时，请重试。';
  return '监控数据加载失败，请检查网络后重试。';
}
