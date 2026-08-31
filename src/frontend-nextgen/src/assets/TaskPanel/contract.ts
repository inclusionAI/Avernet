// @asset-migrated: teamclaw 自研资产
/**
 * 后端契约 DTO —— 与 docs/specs/2026-08-18-task-goal-driven-execution-loop/api-contract.md §4 对齐。
 * 仅用于副屏内部 fetch 反序列化，真实字段差异由 taskPanelMapper 收口。
 */

export type EngineStatus = 'PENDING' | 'PLANNING' | 'RUNNING' | 'DONE' | 'FAILED' | 'HUNG';
export type ProductTaskStatus = 'DRAFTING' | 'DEFINED' | 'EXECUTING' | 'REVIEWING' | 'DONE' | 'FAILED' | 'CANCELLED';
export type TaskStatusCode = EngineStatus | ProductTaskStatus;

export type TaskType = 'yaml' | 'workflow' | 'dynamic';
export type TaskSourceType = 'bot' | 'coop_group' | 'api';

export interface Envelope<T> {
  code: number;
  message: string;
  data: T | null;
  request_id: string;
}

export interface TaskArtifactDto {
  artifact_id: string;
  node_id?: string | null;
  name: string;
  type: 'document' | 'report' | 'link' | 'file' | 'other';
  url?: string | null;
  mime_type?: string | null;
  summary?: string | null;
  created_at: string;
}

export interface TaskRelationDto {
  src_id: string;
  dst_id: string;
  type: 'DEPENDENCY';
  extend_props?: Record<string, unknown>;
}

export interface NodeActionEventDto {
  seq: number;
  ts: number;
  action: string;
  loop_round?: number;
  attempt?: number;
  status_from?: string | null;
  status_to?: string | null;
  payload?: Record<string, unknown>;
}

export type TaskSpecAcceptanceDto =
  | string
  | {
      id?: string;
      acceptance?: string;
      description?: string;
    };

export interface TaskSpecDto {
  metadata?: { title?: string; instruction?: string };
  context?: { background?: string; extend_props?: Record<string, unknown> };
  goal?: {
    objective?: string;
    acceptances?: TaskSpecAcceptanceDto[];
  };
}

export interface TaskNodeDto {
  node_id: string;
  task_id: string;
  sequence: number;
  status: TaskStatusCode | 'SKIPPED';
  task_spec?: TaskSpecDto;
  run_info?: {
    run_mode?: 'single_bot' | 'coop_group' | 'bbs' | null;
    assignee?: string | null;
    assignee_name?: string | null;
    assignee_avatar_url?: string | null;
    start_time?: number | null;
    end_time?: number | null;
    output?: Record<string, unknown>;
    output_summary?: string | null;
    artifacts?: TaskArtifactDto[];
    child_task_id?: string | null;
    acceptance_result?: {
      verdict: 'PASS' | 'FAIL';
      acceptances_metric: string[];
      gaps: string[];
    } | null;
    extend_props?: Record<string, unknown>;
    action_log?: NodeActionEventDto[];
  };
}

export interface TaskDashboardResponse {
  task_id: string;
  run_id: number;
  parent_task_id?: string | null;
  status: TaskStatusCode;
  status_reason?: string | null;
  needs_attention: boolean;
  task_type: TaskType;
  source_type: TaskSourceType;
  owner_user_id: string;
  owner_bot_id: string;
  execution_config?: Record<string, unknown>;
  task_spec: TaskSpecDto;
  owner_bot?: { bot_id: string; name: string; avatar_url?: string | null };
  main_session?: {
    session_id: string;
    name: string;
    source_type: TaskSourceType;
    owner_bot_id: string;
    source_group_id?: string | null;
  } | null;
  derived_session_ids?: string[];
  create_time: string;
  finish_time: string | null;
  loop_round: number;
  progress: {
    total: number;
    pending: number;
    planning: number;
    running: number;
    done: number;
    failed: number;
    hung: number;
    skipped: number;
    percent: number;
  };
  output?: Record<string, unknown>;
  artifacts?: TaskArtifactDto[];
  tasks: TaskNodeDto[];
  relations?: TaskRelationDto[];
  extend_props?: Record<string, unknown>;
}
