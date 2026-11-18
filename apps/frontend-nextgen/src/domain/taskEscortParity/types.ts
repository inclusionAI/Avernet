import type { PageId } from '@/domain/taskEscortWorkspace/types';

/** Only metadata verified in the workflow-types response; unknown is never zero. */
export interface TaskEscortWorkflowSummary {
  id: string;
  title: string;
  runCount: number | null;
  lastStatus: string | null;
  ownerId: string | null;
}

/** Shared read-only port for all formal TaskEscort secondary/tertiary pages. */
export interface TaskEscortParityContext {
  scopeKey: string;
  ready: boolean;
  loading?: boolean;
  error?: string;
  userId?: string;
  workflowId: string;
  workflow: TaskEscortWorkflowSummary | null;
  query: Record<string, string | undefined>;
  navigate: (page: PageId, params?: Record<string, string | undefined>) => void;
  refreshWorkflows: () => void;
}
