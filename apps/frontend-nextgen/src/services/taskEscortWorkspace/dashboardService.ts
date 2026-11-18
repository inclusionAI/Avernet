import type { DashboardApi } from '@/domain/taskEscortWorkspace/dashboardTypes';
import {
  dashboardController,
  type DashboardControllerApi,
} from '@/services/backendApi/harnessFlow/dashboardController';
import {
  mapDashboardDailyTrend,
  mapDashboardFailureHotspots,
  mapDashboardOverview,
  mapDashboardWorkflowHealth,
  mapDashboardWorkflowReleases,
} from './dashboardMapper';

export function isDashboardForbidden(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const value = error as { status?: number; data?: { code?: string }; message?: string };
  return value.status === 403 || value.data?.code === 'FORBIDDEN' || value.message === '只有管理员可以查看全局数据大盘';
}
/** Always real. Validate each wire record and map into nullable Domain models; never return raw DTOs. */
export function createDashboardService(api: DashboardControllerApi = dashboardController): DashboardApi {
  return {
    overview: async (range, signal) => mapDashboardOverview(await api.overview(range, signal)),
    dailyTrend: async (range, signal) => mapDashboardDailyTrend(await api.dailyTrend(range, signal)),
    failureHotspots: async (range, signal) => mapDashboardFailureHotspots(await api.failureHotspots(range, signal)),
    workflowHealth: async (range, signal) => mapDashboardWorkflowHealth(await api.workflowHealth(range, signal)),
    workflowReleaseStats: async (range, signal) =>
      mapDashboardWorkflowReleases(await api.workflowReleaseStats(range, signal)),
  };
}
export const dashboardService = createDashboardService();
