import type { Experience, Issue } from '@/domain/taskEscortWorkspace/types';

export interface IssueFilters {
  query: string;
  status: string;
  workflowId: string;
}

export function filterIssues(issues: Issue[], filters: IssueFilters): Issue[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return issues.filter(
    (issue) =>
      (!filters.status || filters.status === 'all' || issue.status === filters.status) &&
      (!filters.workflowId || filters.workflowId === 'all' || issue.workflowId === filters.workflowId) &&
      (!query ||
        [issue.id, issue.title, issue.description, issue.workflowName, issue.node, issue.category, ...issue.evidence]
          .join(' ')
          .toLocaleLowerCase()
          .includes(query)),
  );
}

export function getIssueActions(issue: Issue, experiences: Experience[]) {
  const saved = experiences.some((experience) => experience.sourceIssueId === issue.id);
  return {
    canApply: issue.status === 'open' || issue.status === 'recurring',
    canVerify: issue.status === 'applied',
    canSave: issue.status === 'verified' && !saved,
    saved,
  };
}

export function prioritizeIssues(issues: Issue[]): Issue[] {
  const severity = { high: 3, medium: 2, low: 1 };
  return [...issues].sort(
    (a, b) =>
      Number(b.status === 'recurring') - Number(a.status === 'recurring') ||
      severity[b.severity] - severity[a.severity] ||
      b.affectedRuns - a.affectedRuns,
  );
}

export function getIssueSummary(issues: Issue[]) {
  return {
    recurring: issues.filter((issue) => issue.status === 'recurring').length,
    open: issues.filter((issue) => issue.status === 'open').length,
    applied: issues.filter((issue) => issue.status === 'applied').length,
    verified: issues.filter((issue) => issue.status === 'verified').length,
  };
}

/** Lifecycle copy must follow live state rather than the fixture description. */
export function getIssueLifecycleMessage(issue: Pick<Issue, 'status'>, saved: boolean): string {
  if (issue.status === 'verified') {
    return saved ? '已完成人工验证，并已保存至经验库。' : '已完成人工验证，尚未保存经验；可由你手动整理并保存。';
  }
  if (issue.status === 'applied') {
    return '建议已应用，待人工验证。请检查相关运行与预期结果，再确认修复效果。';
  }
  if (issue.status === 'recurring') {
    return '同类异常已复发，待重新审阅证据与修复策略；不会自动应用历史建议。';
  }
  return '异常待处理。请先审阅证据与建议，再由你决定是否应用。';
}

export function getIssueDetailPresentation(
  issues: Issue[],
  experiences: Experience[],
  selectedId: string | null,
  editable: boolean,
) {
  const issue = issues.find((entry) => entry.id === selectedId);
  if (!issue) return null;
  const actions = getIssueActions(issue, experiences);
  const gated = {
    ...actions,
    canApply: editable && actions.canApply,
    canVerify: editable && actions.canVerify,
    canSave: editable && actions.canSave,
  };
  const lifecycleStep = actions.saved ? 3 : issue.status === 'verified' ? 2 : issue.status === 'applied' ? 1 : 0;
  return {
    issue,
    actions: gated,
    lifecycleStep,
    verified: issue.status === 'verified',
    lifecycleMessage: getIssueLifecycleMessage(issue, actions.saved),
  };
}
