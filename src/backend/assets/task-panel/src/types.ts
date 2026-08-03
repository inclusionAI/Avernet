/**
 * Task workflow panel — wire types mirroring backend schemas.py.
 * Source of truth: src/agentclaw/community/adapters/http/task/schemas.py
 * (TaskGraphView / TaskNodeView / TaskEdgeView / TaskNodeDetailView / SubDagRefView).
 */

export type TaskStatus =
  | 'drafting'
  | 'defined'
  | 'running'
  | 'human_required'
  | 'bbs_active'
  | 'reviewing'
  | 'done'
  | 'cancelled'
  | 'failed';

export type TaskNodeStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'skipped'
  | 'hung';

export interface SubDagRefView {
  ref_kind: string;
  bcs_run_id: string;
  group_id: string;
  workflow_yaml_snapshot?: string | null;
}

export interface AttemptedExecutorView {
  executor_id?: string;
  paradigm?: string | null;
  round?: number | null;
  outcome?: string | null;
  route_class?: string | null;
  trigger?: string | null;
  at?: string | null;
  note?: string;
  [key: string]: unknown;
}

export interface ArtifactView {
  name?: string;
  location?: string;
  type?: string;
  text?: string;
  [key: string]: unknown;
}

export interface AcceptanceCriteriaView {
  kind: string;
  properties: Record<string, unknown>;
}

export interface TaskNodeView {
  node_id: string;
  display_name: string;
  run_mode?: string | null;
  collab_mode?: string | null;
  status: TaskNodeStatus | string;
  sub_status?: string | null;
  attempt?: number | null;
  assignee?: string | null;
  started_at?: number | null;
  completed_at?: number | null;
  is_final_output?: boolean;
  attempted_executors: AttemptedExecutorView[];
  artifacts: ArtifactView[];
  acceptance_result?: unknown;
  targets_acceptance?: AcceptanceCriteriaView[];
  properties: Record<string, unknown>;
  sub_dag_ref?: SubDagRefView | null;
  instruction?: string | null;
}

export interface TaskEdgeView {
  edge_id: string;
  from_node: string;
  to_node: string;
  kind: string;
  outcome?: string | null;
  guard?: string | null;
}

export interface TaskGraphView {
  task_id: string;
  status: TaskStatus | string;
  loop_round: number;
  definition_meta?: Record<string, unknown> | null;
  nodes: TaskNodeView[];
  edges: TaskEdgeView[];
}

export interface TaskNodeDetailView {
  node_id: string;
  display_name?: string | null;
  status?: TaskNodeStatus | string | null;
  sub_status?: string | null;
  attempt?: number | null;
  run_mode?: string | null;
  collab_mode?: string | null;
  assignee?: string | null;
  attempted_executors: AttemptedExecutorView[];
  artifacts: ArtifactView[];
  acceptance_result?: unknown;
  properties: Record<string, unknown>;
  note?: string;
}

/** Props injected by the SDK UmdPanel loader (mirrors bcsPanel contract). */
export interface TaskWorkflowViewProps {
  taskId?: string;
  /** Full payload object (carries taskId under data/params too). */
  payload?: Record<string, unknown>;
  params?: Record<string, unknown>;
  data?: Record<string, unknown>;
  autoRefresh?: boolean;
  pollingInterval?: number;
  onAction?: (action: unknown) => void;
  onInteraction?: (record: unknown) => void;
  eventEmitter?: { on: (type: string, cb: (data: unknown) => void) => () => void };
}
