import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { filterRuns, getRunMetrics } from '@/services/taskEscortWorkspace/runPresentation';
import { useMemo, useState } from 'react';

export function useRunPresentation(demo: TaskEscortWorkspaceController) {
  const [page, setPage] = useState(1);
  const filtered = useMemo(
    () =>
      filterRuns(demo.runs, {
        query: demo.query,
        status: demo.runFilter,
        workflowId: demo.workflowId,
      }),
    [demo.runs, demo.query, demo.runFilter, demo.workflowId],
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / 10));
  const currentPage = Math.min(page, pageCount);
  const rows = filtered.slice((currentPage - 1) * 10, currentPage * 10);
  const samples = getRunMetrics(filtered);
  const clear = () => {
    demo.setQuery('');
    demo.setRunFilter('all');
    demo.setWorkflowId('all');
    setPage(1);
  };
  return { filtered, pageCount, currentPage, rows, samples, clear, setPage };
}
