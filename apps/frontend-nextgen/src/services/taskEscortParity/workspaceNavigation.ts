import type { PageId } from '@/domain/taskEscortWorkspace/types';

type FormalPage = Exclude<PageId, 'workbench'>;
const aliases: Record<string, FormalPage> = {
  dashboard: 'dashboard',
  runs: 'runs',
  overview: 'runs',
  workbench: 'issues',
  issues: 'issues',
  diagnosis: 'issues',
  suggestions: 'issues',
  experiences: 'experiences',
  remedies: 'experiences',
  editor: 'editor',
  settings: 'settings',
  management: 'settings',
};
export function readWorkspacePage(search: string): FormalPage {
  const query = new URLSearchParams(search);
  const view = query.get('view');
  // 无参数或非法深链统一落「运行概览」：数据大盘仅管理员可见，不能成为普通用户首屏。
  if (view) return Object.prototype.hasOwnProperty.call(aliases, view) ? aliases[view] : 'runs';
  const tab = query.get('tab');
  if (tab === 'evolution') return query.get('evoTab') === 'remedies' ? 'experiences' : 'issues';
  return tab && Object.prototype.hasOwnProperty.call(aliases, tab) ? aliases[tab] : 'runs';
}

/** 深链键（工作流/运行/问题/节点等）在切换空间后必须全部失效，避免串用上一个空间的数据。 */
export const workspaceDeepLinkKeys = [
  'workflowId',
  'runId',
  'analysisId',
  'issueSignature',
  'issueId',
  'nodeId',
  'stepId',
  'experienceId',
  'suggestionId',
  'repairTaskId',
  'baseline',
  'current',
] as const;

/** 清除跨空间的残留深链，并保留当前视图/周期/host 等非数据选择参数。 */
export function clearWorkspaceDeepLinks(search: string): string {
  const query = new URLSearchParams(search);
  let changed = false;
  for (const key of workspaceDeepLinkKeys) {
    if (query.has(key)) {
      query.delete(key);
      changed = true;
    }
  }
  return changed ? `?${query.toString()}` : search;
}

/** Product guidance is an overlay on the current real selection, not another workspace page. */
export function workspaceProductNavigationSearch(search: string, open: boolean): string {
  const query = new URLSearchParams(search);
  if (open) query.set('productNavigation', '1');
  else query.delete('productNavigation');
  const next = query.toString();
  return next ? `?${next}` : '';
}

export function workspaceNavigationSearch(
  search: string,
  page: PageId,
  params: Record<string, string | undefined> = {},
): string {
  const query = new URLSearchParams(search);
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) query.delete(key);
    else query.set(key, value);
  }
  const target = page === 'workbench' ? 'issues' : page;
  query.delete('demo');
  query.delete('productNavigation');
  query.set('view', target);
  const tab = {
    dashboard: 'dashboard',
    runs: 'overview',
    issues: 'evolution',
    experiences: 'evolution',
    editor: 'editor',
    settings: 'management',
  }[target];
  query.set('tab', tab);
  if (target === 'issues' || target === 'experiences')
    query.set('evoTab', target === 'issues' ? 'diagnosis' : 'remedies');
  else query.delete('evoTab');
  return `?${query.toString()}`;
}

export function workspaceSelectionSearch(search: string, id: string, currentId: string): string {
  const query = new URLSearchParams(search);
  query.delete('demo');
  query.delete('productNavigation');
  query.set('workflowId', id);
  if (id !== currentId) {
    // workflowId 是本次选择的目标，必须保留；其余旧工作流深链全部失效。
    for (const key of workspaceDeepLinkKeys) if (key !== 'workflowId') query.delete(key);
  }
  return `?${query.toString()}`;
}
