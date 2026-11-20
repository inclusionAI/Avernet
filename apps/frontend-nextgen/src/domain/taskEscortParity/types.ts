import type { PageId } from '@/domain/taskEscortWorkspace/types';

/** Only metadata verified in the workflow-types response; unknown is never zero. */
export interface TaskEscortWorkflowSummary {
  id: string;
  title: string;
  runCount: number | null;
  lastStatus: string | null;
  ownerId: string | null;
}

/** Shared workspace port for formal TaskEscort pages. */
export interface TaskEscortParityContext {
  scopeKey: string;
  ready: boolean;
  /** Identity is resolved; creating must not require an existing workflow. */
  creationReady?: boolean;
  loading?: boolean;
  error?: string;
  userId?: string;
  workflowId: string;
  workflow: TaskEscortWorkflowSummary | null;
  query: Record<string, string | undefined>;
  navigate: (page: PageId, params?: Record<string, string | undefined>) => void;
  refreshWorkflows: () => void;
  /** Defer refreshing until navigation is committed, preserving dirty-editor blockers. */
  openCreatedWorkflow?: (workflowId: string) => void;
}
