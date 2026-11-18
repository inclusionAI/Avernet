import {
  dashboardRange,
  formatDashboardDuration,
  formatDashboardRate,
  joinDashboardWorkflows,
  resolveDashboardWidgetRange,
  selectDashboardWorkflows,
} from '@/domain/taskEscortWorkspace/dashboard';

const now = new Date(2026, 10, 17, 14, 15, 16, 999);
const seconds = Math.floor(now.getTime() / 1000);
const midnight = Math.floor(new Date(2026, 10, 17).getTime() / 1000);
const h = (id: string, rate: number | null = 0.5) => ({
  workflowId: id,
  workflowTitle: `Workflow ${id}`,
  sceneName: '',
  released: false,
  runCount: 12,
  failedCount: 2,
  completionSuccessRate: rate,
  selfHealTriggeredRuns: 0,
  selfHealSuccessRate: null,
  machineDurationP50: null,
  avgDurationMs: null,
});
const r = (id: string) => ({
  workflowId: id,
  workflowTitle: `Release ${id}`,
  createdAt: null,
  released: true,
  deployCount: 3,
  rollbackCount: 0,
  devCycleMs: 60000,
  lastDeployAt: 1794800000,
  firstRunSuccessRate: 0.9,
});
it('uses local calendar boundaries and inclusive yesterday end, never milliseconds', () => {
  expect(dashboardRange('today', seconds)).toEqual({ from: midnight, to: seconds, label: '今天' });
  expect(dashboardRange('yesterday', seconds)).toEqual({
    from: Math.floor(new Date(2026, 10, 16).getTime() / 1000),
    to: midnight - 1,
    label: '昨天',
  });
  expect(dashboardRange('7d', seconds)).toEqual({ from: seconds - 7 * 86400, to: seconds, label: '近7天' });
  expect(dashboardRange('30d', seconds).from).toBe(seconds - 30 * 86400);
});
it('resolves independent widget windows against the same refresh instant', () => {
  const global = dashboardRange('today', seconds);
  expect(resolveDashboardWidgetRange('global', global, seconds)).toBe(global);
  expect(resolveDashboardWidgetRange('yesterday', global, seconds)).toEqual(dashboardRange('yesterday', seconds));
  expect(resolveDashboardWidgetRange('30d', global, seconds)).toEqual(dashboardRange('30d', seconds));
});
it('formats fraction rates, real zero, unknown and successful P50 milliseconds truthfully', () => {
  expect(formatDashboardRate(0.875)).toBe('87.5%');
  expect(formatDashboardRate(0)).toBe('0.0%');
  expect(formatDashboardRate(null)).toBe('—');
  expect(formatDashboardRate(undefined)).toBe('—');
  expect(formatDashboardDuration(123000)).toBe('123s');
  expect(formatDashboardDuration(null)).toBe('—');
});
it('full outer joins by workflowId, not title, without inventing unavailable metrics', () => {
  const rows = joinDashboardWorkflows([h('same'), h('health')], [r('same'), r('release')]);
  expect(rows).toHaveLength(3);
  expect(rows.find((row) => row.workflowId === 'same')).toMatchObject({
    runCount: 12,
    failedCount: 2,
    deployCount: 3,
    status: 'released',
  });
  expect(rows.find((row) => row.workflowId === 'release')).toMatchObject({
    runCount: null,
    failedCount: null,
    selfHealTriggeredRuns: null,
    completionSuccessRate: null,
  });
  expect(joinDashboardWorkflows([h('health')], null)[0].deployCount).toBeNull();
  expect(joinDashboardWorkflows(null, [r('release')])[0].runCount).toBeNull();
});
it('searches name/id, filters status, sorts null last and paginates 10 without mutating aggregates', () => {
  const rows = joinDashboardWorkflows(
    Array.from({ length: 12 }, (_, i) => h(String(i), i === 0 ? null : i / 20)),
    [r('2')],
  );
  const first = selectDashboardWorkflows(rows, { query: '', status: 'all', sort: 'completionAsc', page: 1 });
  expect(first.rows).toHaveLength(10);
  expect(first.rows[0].workflowId).toBe('1');
  expect(
    selectDashboardWorkflows(rows, { query: '', status: 'all', sort: 'completionAsc', page: 2 }).rows.at(-1)
      ?.workflowId,
  ).toBe('0');
  expect(
    selectDashboardWorkflows(rows, { query: 'WORKFLOW 2', status: 'released', sort: 'runDesc', page: 9 }),
  ).toMatchObject({ total: 1, page: 1 });
  expect(selectDashboardWorkflows(rows, { query: 'missing', status: 'all', sort: 'failedDesc', page: 9 }).rows).toEqual(
    [],
  );
  expect(rows).toHaveLength(12);
});

it('does not infer zero from absence in capped health/release responses', () => {
  const rows = joinDashboardWorkflows([h('health-only')], [r('release-only')]);
  expect(rows.find((row) => row.workflowId === 'health-only')?.deployCount).toBeNull();
  expect(rows.find((row) => row.workflowId === 'release-only')).toMatchObject({
    runCount: null,
    failedCount: null,
    selfHealTriggeredRuns: null,
  });
  const zeros = joinDashboardWorkflows(
    [{ ...h('matched'), runCount: 0, failedCount: 0 }],
    [{ ...r('matched'), deployCount: 0 }],
  );
  expect(zeros[0]).toMatchObject({ runCount: 0, failedCount: 0, deployCount: 0 });
});
it.each(['7d', '30d'] as const)(
  'anchors a widget %s to global yesterday close, not current wall-clock time',
  (range) => {
    const global = dashboardRange('yesterday', seconds);
    const result = resolveDashboardWidgetRange(range, global, seconds);
    expect(result.to).toBe(midnight - 1);
    expect(result.from).toBe(global.to - (range === '7d' ? 7 : 30) * 86400);
  },
);
