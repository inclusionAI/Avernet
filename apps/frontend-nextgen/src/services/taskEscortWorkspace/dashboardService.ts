import type { DashboardApi } from '@/domain/taskEscortWorkspace/dashboardTypes';
import {
  dashboardController,
  DASHBOARD_BASE,
  type DashboardControllerApi,
} from '@/services/backendApi/harnessFlow/dashboardController';
import { backendRequest } from '@/services/backendApi/httpClient';
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

/**
 * 管理员可见性探针：403 直接视为「不可见」且不投递全局错误提示，避免普通身份进入任务护航就收到无权 toast。
 * 其他错误保持抛出，由调用方按 unknown 处理，不误判为无权限。
 */
export async function probeDashboardAccess(
  range: { from: number; to: number },
  signal?: AbortSignal,
): Promise<boolean> {
  try {
    await backendRequest(`${DASHBOARD_BASE}/overview`, {
      method: 'GET',
      params: { ...range },
      signal,
      suppressErrorNotification: true,
      operation: 'task-escort-dashboard-access',
    });
    return true;
  } catch (error) {
    if (isDashboardForbidden(error)) return false;
    throw error;
  }
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
