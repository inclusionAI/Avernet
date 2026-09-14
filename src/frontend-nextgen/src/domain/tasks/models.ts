/**
 * 任务执行 Loop 领域模型（Domain）。纯 TS 类型，不依赖运行时。
 * 规格：docs/specs/2026-08-18-task-goal-driven-execution-loop/api-contract.md §2。
 * Component/Hook/Store 都可 import Domain；DTO⇄Domain 映射在 services/tasks。
 */
export type TaskStatus = 'DRAFTING' | 'DEFINED' | 'EXECUTING' | 'REVIEWING' | 'DONE' | 'FAILED' | 'CANCELLED';

export type TaskType = 'yaml' | 'workflow' | 'dynamic';
export type TaskSourceType = 'bot' | 'coop_group' | 'api';

export interface AcceptanceCriteria {
  id: string;
  description: string;
}

export interface TaskExecConfig {
  yaml?: string;
  workflow_id?: string;
  MAX_DEPTH?: number;
  MAX_LOOP?: number;
  MAX_HARNESS?: number;
  BBS_MAX_DEPTH?: number;
  /** 会话/群/父任务上下文(扁平)。新规范从 execution_config 读取;历史记录读 task_spec.context.extend_props.teamclaw_context 兼容。 */
  main_session_id?: string;
  main_session_name?: string;
  source_group_id?: string;
  parent_task_id?: string | null;
  [key: string]: unknown;
}

export interface TaskSpec {
  metadata: { task_id?: string; title: string; instruction: string };
  context: {
    background: string;
    extend_props: {
      teamclaw_context?: {
        main_session_id?: string;
        main_session_name?: string;
        source_group_id?: string;
        parent_task_id?: string | null;
      };
      [key: string]: unknown;
    };
  };
  goal: { objective: string; acceptances: AcceptanceCriteria[] };
}

export interface TaskInfo {
  task_spec: TaskSpec;
  source_type: TaskSourceType;
  owner_user_id: string;
  owner_bot_id: string;
  task_type: TaskType;
  execution_config: TaskExecConfig;
}

/** 产品层任务记录（execute 返回 / list 行项）。 */
export interface TaskRecord {
  task_id: string;
  task_info: TaskInfo;
  status: TaskStatus;
  create_time: string;
  finish_time: string | null;
}

/** list 接口返回的任务行项（顶层字段 + task_spec，后端 /openapi/v1/collaboration/tasks/list）。 */
export interface TaskListItem {
  id: number;
  task_id: string;
  source_type: TaskSourceType;
  owner_user_id: string;
  owner_bot_id: string;
  execution_config: {
    task_type: TaskType;
    main_session_id?: string;
    main_session_name?: string;
    source_group_id?: string;
    parent_task_id?: string | null;
    [key: string]: unknown;
  };
  task_spec: TaskSpec;
  status: TaskStatus;
  gmt_create: string;
  gmt_modified: string;
}

export interface Envelope<T> {
  code: number;
  message: string;
  data: T | null;
  request_id: string;
}
