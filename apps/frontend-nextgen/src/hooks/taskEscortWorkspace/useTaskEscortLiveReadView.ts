import type { TaskEscortWorkspaceState } from '@/hooks/useTaskEscortWorkspace';
import {
  liveRunPresentation,
  selectLiveRuns,
  summarizeLiveSample,
} from '@/services/taskEscortWorkspace/livePresentation';

export function useTaskEscortLiveReadView(demo: TaskEscortWorkspaceState) {
  const filtered = selectLiveRuns(demo.runs, {
    query: demo.query,
    workflowId: demo.workflowId,
    status: demo.runFilter,
  });
  return {
    rows: filtered.map((run) => ({ ...run, ...liveRunPresentation(run) })),
    sample: summarizeLiveSample(demo.runs),
    resetFilters: () => {
      demo.setQuery('');
      demo.setWorkflowId('all');
      demo.setRunFilter('all');
    },
  };
}
