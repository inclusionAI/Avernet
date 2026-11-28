import { immutableCopy } from './immutable';
import type {
  Issue,
  PageId,
  Period,
  TaskEscortSettings,
  TaskEscortWorkspaceMode,
  TaskEscortWorkspaceSnapshot,
  TaskEscortWorkspaceSupport,
} from './types';

export const workspacePages: PageId[] = [
  'dashboard',
  'workbench',
  'runs',
  'issues',
  'experiences',
  'editor',
  'settings',
];
const periods: Period[] = ['today', 'yesterday', '7d', '30d'];
export const defaultTaskEscortSettings: TaskEscortSettings = immutableCopy({
  enabled: false,
  autoAnalyze: false,
  humanReview: true,
  notifications: false,
  recurrence: false,
  sensitivity: 'balanced',
  webhook: '',
});
export function emptyWorkspaceSnapshot(): TaskEscortWorkspaceSnapshot {
  return immutableCopy({
    workflows: [],
    runs: [],
    issues: [],
    experiences: [],
    settings: defaultTaskEscortSettings,
    settingsDirty: false,
    loading: false,
    error: null,
  });
}
export function getWorkspaceSupport(_mode: TaskEscortWorkspaceMode): TaskEscortWorkspaceSupport {
  void _mode; // Compatibility argument only; support always describes real capabilities.
  return immutableCopy(
    Object.fromEntries(
      workspacePages.map((page) => [
        page,
        page === 'dashboard' || page === 'runs'
          ? { available: true }
          : {
              available: false,
              reason: '能力建设中：此页面的真实能力暂未接入，首阶段仅提供工作流与运行记录的只读查看',
            },
      ]),
    ) as TaskEscortWorkspaceSupport,
  );
}
/** Shared pending predicate: manually verified records belong to history, saved or not. */
export function isActionableIssue(issue: Pick<Issue, 'status'>): boolean {
  return issue.status !== 'verified';
}
export function getWorkbenchScope(issues: Issue[]) {
  return {
    actionable: issues.filter(isActionableIssue),
    completed: issues.filter((issue) => !isActionableIssue(issue)),
  };
}
export interface WorkspaceRoute {
  page: PageId;
  period: Period;
}
/** Legacy query flags never enable synthetic data in the formal workspace. */
export function readWorkspaceMode(_search = ''): TaskEscortWorkspaceMode {
  void _search;
  return 'live';
}
export function readRoute(search = ''): WorkspaceRoute {
  const params = new URLSearchParams(search);
  const page = params.get('view') as PageId;
  const period = params.get('period') as Period;
  // 运行概览是对所有身份都可达的首屏；数据大盘仅管理员可见，不能作为无参数默认落点。
  return { page: workspacePages.includes(page) ? page : 'runs', period: periods.includes(period) ? period : '7d' };
}
export function routeSearch(search: string, route: WorkspaceRoute): string {
  const params = new URLSearchParams(search);
  params.delete('demo');
  params.set('view', route.page);
  params.set('period', route.period);
  return `?${params.toString()}`;
}
