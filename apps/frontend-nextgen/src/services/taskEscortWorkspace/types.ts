import type {
  DomainRunDetail,
  Metrics,
  Period,
  TaskEscortOperationResult,
  TaskEscortSettings,
  TaskEscortWorkspaceMode,
  TaskEscortWorkspaceSnapshot,
  TaskEscortWorkspaceSupport,
} from '@/domain/taskEscortWorkspace';

/** Optional structural fields accept actual RunRow and the existing shared DTO aliases. */
export interface TaskEscortRawRun {
  flow_id: string;
  workflow_id: string;
  workflow_title?: string | null;
  status?: string | null;
  created_by?: string | null;
  started_at?: number | string | null;
  completed_at?: number | string | null;
  total_duration_ms?: number | null;
  duration_ms?: number | null;
  succeeded_count?: number | null;
  succeeded_nodes?: number | null;
  failed_count?: number | null;
  failed_nodes?: number | null;
  total_token_usage?: number | null;
  total_tokens?: number | null;
  triggered_by?: string | null;
  trigger?: string | null;
  origin_session_id?: string | null;
  session_id?: string | null;
}
export interface TaskEscortRawWorkflow {
  workflowId: string;
  title?: string | null;
  packId?: string | null;
  updatedAt?: number | string | null;
}
export interface TaskEscortRawNodeExecution {
  node_id: string;
  node_title?: string | null;
  status?: string | null;
  duration_ms?: number | null;
  error_text?: string | null;
  input_json?: string | null;
  output_json?: string | null;
}
/** Deliberately read-only. No global dashboard, suggestions, lessons or writes. */
export interface TaskEscortWorkspaceApi {
  listWorkflows: () => Promise<TaskEscortRawWorkflow[]>;
  listFlowRuns: (params: { limit: number }) => Promise<TaskEscortRawRun[]>;
  getFlowRun: (flowId: string) => Promise<{ run: TaskEscortRawRun; nodes?: TaskEscortRawNodeExecution[] }>;
}
export interface TaskEscortWorkspaceService {
  /** Always live at runtime; the union is retained for prototype type compatibility. */
  readonly mode: TaskEscortWorkspaceMode;
  readonly support: TaskEscortWorkspaceSupport;
  getSnapshot: () => TaskEscortWorkspaceSnapshot;
  subscribe: (listener: () => void) => () => void;
  load: () => Promise<void>;
  /** Invalidates pending results without issuing a request; later load() is allowed. */
  cancelPending: () => void;
  getRunDetail: (flowId: string) => Promise<DomainRunDetail>;
  getMetrics: (period: Period) => Metrics;
  /** Writes are unavailable until a real write contract is approved. */
  applySuggestion: (id: string) => TaskEscortOperationResult;
  verifyIssue: (id: string) => TaskEscortOperationResult;
  saveExperience: (id: string) => TaskEscortOperationResult;
  updateSettings: (patch: Partial<TaskEscortSettings>) => TaskEscortOperationResult;
  saveSettings: () => TaskEscortOperationResult;
  /** Clears local read state and invalidates pending reads; never changes server data. */
  reset: () => void;
}
