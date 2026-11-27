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
  if (view) return Object.prototype.hasOwnProperty.call(aliases, view) ? aliases[view] : 'dashboard';
  const tab = query.get('tab');
  if (tab === 'evolution') return query.get('evoTab') === 'remedies' ? 'experiences' : 'issues';
  return tab && Object.prototype.hasOwnProperty.call(aliases, tab) ? aliases[tab] : 'dashboard';
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
    for (const key of [
      'baseline',
      'current',
      'repairTaskId',
      'runId',
      'analysisId',
      'issueSignature',
      'issueId',
      'nodeId',
      'stepId',
      'experienceId',
      'suggestionId',
    ])
      query.delete(key);
  }
  return `?${query.toString()}`;
}
