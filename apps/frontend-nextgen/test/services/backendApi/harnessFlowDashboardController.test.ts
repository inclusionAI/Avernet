import { dashboardController } from '@/services/backendApi/harnessFlow/dashboardController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { readFileSync } from 'node:fs';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
beforeEach(() => request.mockReset());

it.each([
  ['overview', 'overview', {}],
  ['dailyTrend', 'daily-trend', {}],
  ['failureHotspots', 'failure-hotspots', { limit: 10 }],
  ['workflowHealth', 'workflow-health', {}],
  ['workflowReleaseStats', 'workflow-release-stats', {}],
] as const)('%s reads the unwrapped global gateway contract in epoch seconds', async (method, path, extra) => {
  const payload = { available: false, workflows: [] };
  request.mockResolvedValue(payload);
  const abort = new AbortController();
  expect(await dashboardController[method]({ from: 1794268800, to: 1794873600 }, abort.signal)).toBe(payload);
  expect(request).toHaveBeenCalledWith(`/openapi/v1/harnessflow/api/dashboard/${path}`, {
    method: 'GET',
    params: { from: 1794268800, to: 1794873600, ...extra },
    signal: abort.signal,
  });
});

it('propagates the actual admin denial instead of providing any fallback', async () => {
  const forbidden = Object.assign(new Error('只有管理员可以查看全局数据大盘'), { status: 403 });
  request.mockRejectedValue(forbidden);
  await expect(dashboardController.overview({ from: 1, to: 2 })).rejects.toBe(forbidden);
});

it('owns protocol DTOs independently of Domain models/API', () => {
  const source = readFileSync(require.resolve('@/services/backendApi/harnessFlow/dashboardController'), 'utf8');
  expect(source).not.toMatch(/from\s+['"]@\/domain\//);
  expect(source).toMatch(/export interface DashboardOverviewDto/);
  expect(source).toMatch(/export interface DashboardControllerApi/);
});
