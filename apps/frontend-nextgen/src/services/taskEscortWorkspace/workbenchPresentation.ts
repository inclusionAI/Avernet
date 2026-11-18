import type { Issue } from '@/domain/taskEscortWorkspace/types';
import type { IssueFilters } from './issuePresentation';
import { filterIssues, getIssueSummary, prioritizeIssues } from './issuePresentation';

import { getWorkbenchScope } from '@/domain/taskEscortWorkspace/workspace';
export { getWorkbenchScope, isActionableIssue } from '@/domain/taskEscortWorkspace/workspace';

export function getWorkbenchPresentation(issues: Issue[], filters: IssueFilters) {
  const scoped = filterIssues(issues, { ...filters, status: 'all' });
  const { actionable, completed } = getWorkbenchScope(scoped);
  const summary = getIssueSummary(actionable);
  const tabs = [
    { value: 'all', label: '全部待办', count: actionable.length },
    { value: 'recurring', label: '复发异常', count: summary.recurring },
    { value: 'open', label: '待处理', count: summary.open },
    { value: 'applied', label: '待验证', count: summary.applied },
    { value: 'verified', label: '已完成', count: completed.length },
  ];
  const activeTab = tabs.some((tab) => tab.value === filters.status) ? filters.status : 'all';
  const rows = prioritizeIssues(
    filterIssues(activeTab === 'verified' ? completed : actionable, {
      query: '',
      workflowId: 'all',
      status: activeTab,
    }),
  );
  return { tabs, activeTab, issues: rows };
}

export function getWorkbenchSummary(issues: Issue[]) {
  const { actionable, completed } = getWorkbenchScope(issues);
  return {
    summary: getIssueSummary(actionable),
    actionableCount: actionable.length,
    completedCount: completed.length,
    affectedRuns: issues.reduce((total, issue) => total + issue.affectedRuns, 0),
    recommendations: issues.filter((issue) => issue.recommendation).length,
  };
}
