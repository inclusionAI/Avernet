import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { getWorkbenchPresentation, getWorkbenchSummary } from '@/services/taskEscortWorkspace/workbenchPresentation';
import { useMemo } from 'react';

export function useWorkbenchSummary(demo: TaskEscortWorkspaceController) {
  return useMemo(() => getWorkbenchSummary(demo.issues), [demo.issues]);
}

export function useWorkbenchPresentation(demo: TaskEscortWorkspaceController) {
  const presentation = useMemo(
    () =>
      getWorkbenchPresentation(demo.issues, {
        query: demo.query,
        workflowId: demo.workflowId,
        status: demo.issueFilter,
      }),
    [demo.issues, demo.query, demo.workflowId, demo.issueFilter],
  );
  const clear = () => {
    demo.setQuery('');
    demo.setIssueFilter('all');
    demo.setWorkflowId('all');
  };
  return { ...presentation, clear };
}
