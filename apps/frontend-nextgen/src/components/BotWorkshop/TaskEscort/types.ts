/** Node execution detail for run detail display */
export interface NodeExecution {
  flow_id: string;
  node_id: string;
  node_title: string | null;
  executor_type: string;
  triggered_by: string | null;
  phase: string | null;
  branch_id: string | null;
  session_key: string | null;
  session_id: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  input_json: string | null;
  output_json: string | null;
  error_text: string | null;
  token_usage_json: string | null;
  system_context_json: string | null;
  progress_message: string | null;
  attempt: number;
}

/** Workflow type row for dashboard display */
export interface TaskEscortWorkflowType {
  workflow_id: string;
  workflow_title: string | null;
  run_count: number;
  last_status: string | null;
  last_run_at: number | null;
  updated_at: number | null;
}

/** Flow run record for detail display */
export interface TaskEscortFlowRun {
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  status: string;
  created_by: string | null;
  started_at: number | null;
  completed_at: number | null;
  duration_ms: number | null;
  node_count: number;
  succeeded_nodes: number;
  failed_nodes: number;
  running_nodes: number;
  total_tokens: number | null;
  error_message: string | null;
  session_id: string | null;
  trigger: string | null;
}

/** Workflow list item for flow config */
export interface TaskEscortWorkflowItem {
  workflowId: string;
  title: string;
  packId: string | null;
  updatedAt: number;
}

/** Workflow node for flow config DAG display */
export interface TaskEscortWorkflowNode {
  id: string;
  title?: string;
  type?: string;
  executor?: string | Record<string, unknown>;
  phase?: string;
  dependsOn?: string[];
  branchId?: string;
  description?: string;
  timeoutMs?: number;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  retry?: { maxAttempts?: number; delayMs?: number; backoff?: string };
  config?: Record<string, unknown>;
  onResult?: Array<{ value: string; target: string }>;
  onSuccess?: Array<{
    id?: string;
    action?: string;
    required?: boolean;
    args?: Record<string, unknown>;
    saveAs?: Record<string, string>;
  }>;
  onFailure?: Array<{
    id?: string;
    action?: string;
    required?: boolean;
    args?: Record<string, unknown>;
    saveAs?: Record<string, string>;
  }>;
  alerting?: Record<string, unknown>;
  [key: string]: unknown;
}

/** Workflow spec for flow config display */
export interface TaskEscortWorkflowSpec {
  id: string;
  version: string;
  title: string;
  nodes: TaskEscortWorkflowNode[];
  [key: string]: unknown;
}
