import type { Issue, TaskEscortWorkspaceController, ViewProps, WorkspaceViewProps } from '@/domain/taskEscortWorkspace';
import {
  getWorkbenchScope,
  getWorkspaceSupport,
  isActionableIssue,
  readRoute,
  readWorkspaceMode,
  routeSearch,
} from '@/domain/taskEscortWorkspace';

describe('Task Escort Workspace domain contracts', () => {
  it('uses exactly the same actionable predicate for pending rows and count', () => {
    const issues = ['open', 'applied', 'verified', 'recurring'].map((status) => ({ status } as Issue));
    const scope = getWorkbenchScope(issues);
    expect(scope.actionable).toEqual(issues.filter(isActionableIssue));
    expect(scope.actionable).toHaveLength(3);
    expect(scope.completed).toEqual([issues[2]]);
    expect(issues.map((issue) => issue.status)).toEqual(['open', 'applied', 'verified', 'recurring']);
  });

  it('ignores obsolete demo flags and strips them while preserving unrelated route parameters', () => {
    expect(readWorkspaceMode()).toBe('live');
    expect(readWorkspaceMode('')).toBe('live');
    expect(readRoute()).toEqual({ page: 'runs', period: '7d' });
    expect(readWorkspaceMode('?demo=true')).toBe('live');
    for (const search of ['?demo=1', '?demo=0', '?demo=1&demo=1', '?demo=1&mode=demo']) {
      expect(readWorkspaceMode(search)).toBe('live');
    }
    expect(readRoute('?view=not-real&period=bad')).toEqual({ page: 'runs', period: '7d' });
    expect(readRoute('?view=runs&period=today')).toEqual({ page: 'runs', period: 'today' });
    const search = routeSearch('?demo=1&keep=value', { page: 'issues', period: '30d' });
    expect(new URLSearchParams(search).get('keep')).toBe('value');
    expect(new URLSearchParams(search).has('demo')).toBe(false);
    expect(readRoute(search)).toEqual({ page: 'issues', period: '30d' });
  });

  it('exposes read-only live capabilities and compatible framework-free view aliases', () => {
    expect(getWorkspaceSupport('live').editor.available).toBe(false);
    expect(getWorkspaceSupport('demo')).toEqual(getWorkspaceSupport('live'));
    expect(getWorkspaceSupport('invalid' as never)).toEqual(getWorkspaceSupport('live'));
    const echo = (props: ViewProps): WorkspaceViewProps => props;
    const contract = {} as TaskEscortWorkspaceController;
    expect(echo({ demo: contract }).demo).toBe(contract);
  });
});
