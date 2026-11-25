import type {
  DashboardRangeKey,
  DashboardWidgetRange,
  DashboardWindow,
  DashboardWorkflowHealthRow,
  DashboardWorkflowReleaseRow,
  DashboardWorkflowRow,
  DashboardWorkflowSort,
} from './dashboardTypes';

export const dashboardRangeLabels: Record<DashboardRangeKey, string> = {
  today: '今天',
  yesterday: '昨天',
  '7d': '近7天',
  '30d': '近30天',
};

export function dashboardRange(range: DashboardRangeKey, nowSeconds = Math.floor(Date.now() / 1000)): DashboardWindow {
  const now = Math.floor(nowSeconds);
  if (range === 'today' || range === 'yesterday') {
    const start = new Date(now * 1000);
    start.setHours(0, 0, 0, 0);
    const today = Math.floor(start.getTime() / 1000);
    // Calendar subtraction keeps yesterday correct across local daylight-saving changes.
    if (range === 'yesterday') start.setDate(start.getDate() - 1);
    return {
      from: Math.floor(start.getTime() / 1000),
      to: range === 'today' ? now : today - 1,
      label: dashboardRangeLabels[range],
    };
  }
  return { from: now - (range === '7d' ? 7 : 30) * 86400, to: now, label: dashboardRangeLabels[range] };
}

export function resolveDashboardWidgetRange(
  range: DashboardWidgetRange,
  global: DashboardWindow,
  nowSeconds: number,
): DashboardWindow {
  if (range === 'global') return global;
  return dashboardRange(range, range === 'yesterday' ? nowSeconds : global.to);
}

export function formatDashboardCount(value: number | null | undefined): string {
  return value !== null && value !== undefined && Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
}
export function formatDashboardRate(value: number | null | undefined): string {
  return value !== null && value !== undefined && Number.isFinite(value) && value >= 0 && value <= 1
    ? `${(value * 100).toFixed(1)}%`
    : '—';
}
export function formatDashboardDuration(ms: number | null | undefined): string {
  return ms !== null && ms !== undefined && Number.isFinite(ms) && ms >= 0 ? `${(ms / 1000).toFixed(0)}s` : '—';
}
export function formatDashboardCycle(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return '—';
  if (ms >= 86400000) return `${(ms / 86400000).toFixed(1)}天`;
  if (ms >= 3600000) return `${(ms / 3600000).toFixed(1)}小时`;
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}分钟`;
  return formatDashboardDuration(ms);
}
export function formatDashboardDate(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(
    2,
    '0',
  )}`;
}

/** Full outer join. null source = unavailable, [] = successfully read and empty. */
export function joinDashboardWorkflows(
  health: DashboardWorkflowHealthRow[] | null,
  releases: DashboardWorkflowReleaseRow[] | null,
): DashboardWorkflowRow[] {
  const byId = new Map<string, DashboardWorkflowRow>();
  for (const row of health ?? [])
    byId.set(row.workflowId, {
      workflowId: row.workflowId,
      workflowTitle: row.workflowTitle || row.workflowId,
      status: row.released === true ? 'released' : row.released === false ? 'testing' : 'unknown',
      runCount: row.runCount ?? null,
      failedCount: row.failedCount ?? null,
      completionSuccessRate: row.completionSuccessRate ?? null,
      selfHealSuccessRate: row.selfHealSuccessRate ?? null,
      selfHealTriggeredRuns: row.selfHealTriggeredRuns ?? null,
      machineDurationP50: row.machineDurationP50 ?? null,
      // Each source is capped: a missing match is unknown, never a confirmed zero.
      deployCount: null,
      devCycleMs: null,
      lastDeployAt: null,
      firstRunSuccessRate: null,
    });
  for (const row of releases ?? []) {
    const existing = byId.get(row.workflowId);
    const merged: DashboardWorkflowRow = existing ?? {
      workflowId: row.workflowId,
      workflowTitle: row.workflowTitle || row.workflowId,
      status: row.released === true ? 'released' : row.released === false ? 'unpublished' : 'unknown',
      runCount: null,
      failedCount: null,
      completionSuccessRate: null,
      selfHealSuccessRate: null,
      selfHealTriggeredRuns: null,
      machineDurationP50: null,
      deployCount: null,
      devCycleMs: null,
      lastDeployAt: null,
      firstRunSuccessRate: null,
    };
    byId.set(row.workflowId, {
      ...merged,
      workflowTitle:
        merged.workflowTitle === row.workflowId && row.workflowTitle ? row.workflowTitle : merged.workflowTitle,
      status: row.released ? 'released' : merged.status,
      deployCount: row.deployCount ?? null,
      devCycleMs: row.devCycleMs ?? null,
      lastDeployAt: row.lastDeployAt ?? null,
      firstRunSuccessRate: row.firstRunSuccessRate ?? null,
    });
  }
  return [...byId.values()];
}

export const dashboardSortLabels: Record<DashboardWorkflowSort, string> = {
  completionAsc: '运行成功率 升序（短板优先）',
  runDesc: '运行数 降序',
  failedDesc: '失败数 降序',
  machineAsc: '机器耗时 升序（最快）',
  healDesc: '自愈次数 降序',
  deployDesc: '部署次数 降序',
  recentDeployDesc: '最近部署 降序',
};
export interface DashboardTableFilter {
  query: string;
  status: 'all' | 'released' | 'notReleased';
  sort: DashboardWorkflowSort;
  page: number;
}
export function selectDashboardWorkflows(rows: DashboardWorkflowRow[], filter: DashboardTableFilter) {
  const query = filter.query.trim().toLocaleLowerCase();
  const filtered = rows.filter(
    (row) =>
      (filter.status === 'all' ||
        (filter.status === 'released' ? row.status === 'released' : row.status !== 'released')) &&
      (!query ||
        row.workflowTitle.toLocaleLowerCase().includes(query) ||
        row.workflowId.toLocaleLowerCase().includes(query)),
  );
  const sortFields: Record<DashboardWorkflowSort, [keyof DashboardWorkflowRow, number]> = {
    completionAsc: ['completionSuccessRate', 1],
    runDesc: ['runCount', -1],
    failedDesc: ['failedCount', -1],
    machineAsc: ['machineDurationP50', 1],
    healDesc: ['selfHealTriggeredRuns', -1],
    deployDesc: ['deployCount', -1],
    recentDeployDesc: ['lastDeployAt', -1],
  };
  const [field, direction] = sortFields[filter.sort];
  filtered.sort((a, b) => {
    const left = a[field],
      right = b[field];
    if (left === null || left === undefined) return right === null || right === undefined ? 0 : 1;
    if (right === null || right === undefined) return -1;
    return (Number(left) - Number(right)) * direction;
  });
  const pages = Math.max(1, Math.ceil(filtered.length / 10));
  const page = Math.min(Math.max(1, filter.page), pages);
  return { rows: filtered.slice((page - 1) * 10, page * 10), total: filtered.length, pages, page };
}
