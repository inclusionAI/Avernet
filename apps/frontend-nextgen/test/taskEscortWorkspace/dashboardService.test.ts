import { dashboardController } from '@/services/backendApi/harnessFlow/dashboardController';
import { createDashboardService, isDashboardForbidden } from '@/services/taskEscortWorkspace/dashboardService';
jest.mock('@/services/backendApi/harnessFlow/dashboardController', () => ({
  dashboardController: {
    overview: jest.fn(),
    dailyTrend: jest.fn(),
    failureHotspots: jest.fn(),
    workflowHealth: jest.fn(),
    workflowReleaseStats: jest.fn(),
  },
}));
const api = jest.mocked(dashboardController);
beforeEach(() => jest.resetAllMocks());
it('preserves backend aggregates and null capabilities without reading any run samples', async () => {
  const overview = {
    totalRuns: 1234,
    successRate: 0.875,
    failedCount: 9,
    terminalCount: 1000,
    statusDistribution: {},
    machineDurationP50: null,
  };
  api.overview.mockResolvedValue(overview as any);
  const mapped = await createDashboardService().overview({ from: 1, to: 2 });
  expect(mapped).toMatchObject(overview);
  expect(mapped).not.toBe(overview);
});
it('keeps unavailable capabilities and each independent error truthful', async () => {
  api.workflowHealth.mockResolvedValue({ available: false, from: 1, to: 2, workflows: [] });
  api.workflowReleaseStats.mockRejectedValue(new Error('release offline'));
  const service = createDashboardService();
  expect(await service.workflowHealth({ from: 1, to: 2 })).toMatchObject({ available: false });
  await expect(service.workflowReleaseStats({ from: 1, to: 2 })).rejects.toThrow('release offline');
});
it('never turns forbidden or malformed responses into empty data', async () => {
  const denied = Object.assign(new Error('只有管理员可以查看全局数据大盘'), { status: 403 });
  api.overview.mockRejectedValueOnce(denied).mockResolvedValueOnce({ success: true } as any);
  const service = createDashboardService();
  await expect(service.overview({ from: 1, to: 2 })).rejects.toBe(denied);
  expect(isDashboardForbidden(denied)).toBe(true);
  await expect(service.overview({ from: 1, to: 2 })).rejects.toThrow('数据结构');
});

it.each([
  ['dailyTrend', { from: 1, to: 2, dates: [{}] }],
  ['dailyTrend', { from: 1, to: 2, dates: [{ date: '2026-02-30' }] }],
  ['failureHotspots', { from: 1, to: 2, hotspots: [null] }],
  ['failureHotspots', { from: 1, to: 2, hotspots: [{ nodeLabel: 7, workflowTitle: 'real' }] }],
  ['workflowHealth', { available: true, from: 1, to: 2, workflows: [{}] }],
  ['workflowHealth', { available: true, from: 1, to: 2, workflows: [null] }],
  ['workflowReleaseStats', { available: true, workflows: [{ workflowId: '' }] }],
  ['workflowReleaseStats', { available: true, workflows: [null] }],
] as const)(
  '%s rejects malformed element identity/date/shape instead of letting a render crash',
  async (method, response) => {
    api[method].mockResolvedValue(response as any);
    await expect(createDashboardService()[method]({ from: 1, to: 2 })).rejects.toThrow('数据结构');
  },
);
it.each([
  [
    'dailyTrend',
    { from: 1, to: 2, dates: [{ date: '2026-11-17', failedCount: '1', successRate: 87.5 }] },
    'dates',
    { date: '2026-11-17', failedCount: null, runCount: null, successRate: null },
  ],
  [
    'failureHotspots',
    { from: 1, to: 2, hotspots: [{ nodeLabel: 'real node', workflowTitle: 'real', sharePct: '75', count: -1 }] },
    'hotspots',
    { sharePct: null, count: null },
  ],
  [
    'workflowHealth',
    {
      available: true,
      from: 1,
      to: 2,
      workflows: [{ workflowId: 'real-health', runCount: '3', failedCount: null, released: 'false' }],
    },
    'workflows',
    { workflowId: 'real-health', runCount: null, failedCount: null, released: null },
  ],
  [
    'workflowReleaseStats',
    {
      available: true,
      workflows: [{ workflowId: 'real-release', deployCount: '2', devCycleMs: -3, firstRunSuccessRate: 75 }],
    },
    'workflows',
    { workflowId: 'real-release', deployCount: null, rollbackCount: null, devCycleMs: null, firstRunSuccessRate: null },
  ],
] as const)(
  '%s maps missing/invalid optional metrics to null, never coerces strings or invents zero',
  async (method, response, key, expected) => {
    api[method].mockResolvedValue(response as any);
    const result = await createDashboardService()[method]({ from: 1, to: 2 });
    expect((result as any)[key][0]).toMatchObject(expected);
  },
);
it('preserves actual metric zeros and fractions when mapping wire records', async () => {
  api.dailyTrend.mockResolvedValue({
    from: 1,
    to: 2,
    dates: [{ date: '2026-11-17', failedCount: 0, runCount: 0, successRate: 0.875 }],
  } as any);
  expect((await createDashboardService().dailyTrend({ from: 1, to: 2 })).dates[0]).toMatchObject({
    failedCount: 0,
    runCount: 0,
    successRate: 0.875,
  });
});
