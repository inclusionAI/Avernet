import type {
  IDashboardOverview,
  IDashboardDailyTrend,
  IDashboardDurationDistribution,
  IDashboardTopWorkflows,
  IDashboardSubsystemSummary,
  IDashboardSceneBreakdown,
  IDashboardReleaseEfficiency,
  IDashboardReleaseQualityTrend,
  IWorkflowReleaseStats,
  IDashboardFailureHotspots,
  IDashboardMetricTrend,
  MetricKey,
  TrendGranularity,
  IWorkflowHealthResponse,
  IWorkflowMetricsResponse,
} from '../../types/dashboard'
import { fetchJson } from '../../api/client'
import * as mock from './mock'

const BASE = '/api'

/**
 * Demo 模式:打开后大盘展示"目标形态"(指标体系设计 spec §6 的可视化),
 * 数据全部来自 mock.ts,不调后端。后端发布机制/场景能力落地后,
 * 把这里的分流去掉,换成真接口(release_id IS NOT NULL 切片)即可,UI 不用改。
 *
 * 打开方式(任一):
 *   - 环境变量 VITE_DASHBOARD_DEMO=1(适合长期挂 demo 分支)
 *   - URL 加 ?demo=1(适合临时给老板看,例:https://clawweb.../dashboard?demo=1)
 *   - URL 加 ?demo=0 强制关闭
 */
const DEMO_LS_KEY = 'clawweb_dashboard_demo'

export function isDemoMode(): boolean {
  if (typeof window !== 'undefined') {
    const p = new URLSearchParams(window.location.search).get('demo')
    if (p === '1') {
      try { localStorage.setItem(DEMO_LS_KEY, '1') } catch { /* ignore */ }
      return true
    }
    if (p === '0') {
      try { localStorage.setItem(DEMO_LS_KEY, '0') } catch { /* ignore */ }
      return false
    }
    try {
      const ls = localStorage.getItem(DEMO_LS_KEY)
      if (ls === '1') return true
      if (ls === '0') return false
    } catch { /* ignore */ }
  }
  const env = (import.meta as { env?: Record<string, string> }).env
  return env?.VITE_DASHBOARD_DEMO === '1' || env?.VITE_DASHBOARD_DEMO === 'true'
}

export const dashboardApi = {
  overview(from: number, to: number): Promise<IDashboardOverview> {
    if (isDemoMode()) return Promise.resolve(mock.mockOverview(from, to))
    return fetchJson<IDashboardOverview>(`${BASE}/dashboard/overview?from=${from}&to=${to}`)
  },
  dailyTrend(from: number, to: number): Promise<IDashboardDailyTrend> {
    if (isDemoMode()) return Promise.resolve(mock.mockDailyTrend(from, to))
    return fetchJson<IDashboardDailyTrend>(`${BASE}/dashboard/daily-trend?from=${from}&to=${to}`)
  },
  durationDistribution(from: number, to: number): Promise<IDashboardDurationDistribution> {
    if (isDemoMode()) return Promise.resolve(mock.mockDurationDistribution(from, to))
    return fetchJson<IDashboardDurationDistribution>(`${BASE}/dashboard/duration-distribution?from=${from}&to=${to}`)
  },
  topWorkflows(from: number, to: number, limit = 10): Promise<IDashboardTopWorkflows> {
    if (isDemoMode()) return Promise.resolve(mock.mockTopWorkflows(from, to, limit))
    return fetchJson<IDashboardTopWorkflows>(`${BASE}/dashboard/top-workflows?from=${from}&to=${to}&limit=${limit}`)
  },
  subsystemSummary(): Promise<IDashboardSubsystemSummary> {
    if (isDemoMode()) return Promise.resolve(mock.mockSubsystemSummary())
    return fetchJson<IDashboardSubsystemSummary>(`${BASE}/dashboard/subsystem-summary`)
  },

  // ── 目标指标新端点(demo 模式先用 mock;真接口由后端发布机制/场景能力落地后补)──
  sceneBreakdown(from: number, to: number): Promise<IDashboardSceneBreakdown> {
    if (isDemoMode()) return Promise.resolve(mock.mockSceneBreakdown(from, to))
    return fetchJson<IDashboardSceneBreakdown>(`${BASE}/dashboard/scene-breakdown?from=${from}&to=${to}`)
  },
  releaseEfficiency(from: number, to: number): Promise<IDashboardReleaseEfficiency> {
    if (isDemoMode()) return Promise.resolve(mock.mockReleaseEfficiency(from, to))
    return fetchJson<IDashboardReleaseEfficiency>(`${BASE}/dashboard/release-efficiency?from=${from}&to=${to}`)
  },
  failureHotspots(from: number, to: number): Promise<IDashboardFailureHotspots> {
    if (isDemoMode()) return Promise.resolve(mock.mockFailureHotspots(from, to))
    return fetchJson<IDashboardFailureHotspots>(`${BASE}/dashboard/failure-hotspots?from=${from}&to=${to}`)
  },
  metricTrend(metric: MetricKey, granularity: TrendGranularity, from: number, to: number): Promise<IDashboardMetricTrend> {
    if (isDemoMode()) return Promise.resolve(mock.mockMetricTrend(metric, granularity, from, to))
    return fetchJson<IDashboardMetricTrend>(`${BASE}/dashboard/metric-trend?metric=${metric}&granularity=${granularity}&from=${from}&to=${to}`)
  },
  releaseQualityTrend(granularity: TrendGranularity, from: number, to: number): Promise<IDashboardReleaseQualityTrend> {
    if (isDemoMode()) return Promise.resolve(mock.mockReleaseQualityTrend(granularity, from, to))
    return fetchJson<IDashboardReleaseQualityTrend>(`${BASE}/dashboard/release-quality-trend?granularity=${granularity}&from=${from}&to=${to}`)
  },
  workflowReleaseStats(from: number, to: number): Promise<IWorkflowReleaseStats> {
    if (isDemoMode()) return Promise.resolve(mock.mockWorkflowReleaseStats(from, to))
    return fetchJson<IWorkflowReleaseStats>(`${BASE}/dashboard/workflow-release-stats?from=${from}&to=${to}`)
  },

  // ── L2/L3 下钻端点 ──
  workflowHealth(from: number, to: number): Promise<IWorkflowHealthResponse> {
    if (isDemoMode()) return Promise.resolve(mock.mockWorkflowHealth(from, to))
    return fetchJson<IWorkflowHealthResponse>(`${BASE}/dashboard/workflow-health?from=${from}&to=${to}`)
  },
  workflowMetrics(workflowId: string, from: number, to: number): Promise<IWorkflowMetricsResponse> {
    if (isDemoMode()) return Promise.resolve(mock.mockWorkflowMetrics(workflowId, from, to))
    return fetchJson<IWorkflowMetricsResponse>(`${BASE}/dashboard/workflow-metrics?workflowId=${workflowId}&from=${from}&to=${to}`)
  },
}