import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { filterIssues, getIssueSummary, prioritizeIssues } from '@/services/taskEscortWorkspace/issuePresentation';
import { useMemo } from 'react';

export function useIssuePresentation(demo: TaskEscortWorkspaceController) {
  const summary = useMemo(() => getIssueSummary(demo.issues), [demo.issues]);
  const issues = useMemo(
    () =>
      prioritizeIssues(
        filterIssues(demo.issues, {
          query: demo.query,
          status: demo.issueFilter,
          workflowId: demo.workflowId,
        }),
      ),
    [demo.issues, demo.query, demo.issueFilter, demo.workflowId],
  );
  const clear = () => {
    demo.setQuery('');
    demo.setIssueFilter('all');
    demo.setWorkflowId('all');
  };
  return { summary, issues, clear };
}
