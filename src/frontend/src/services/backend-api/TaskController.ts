/* eslint-disable */
/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Task Controller - 目标驱动任务执行 (goal-driven task execution)
 *
 * 对应 backend src/agentclaw/community/adapters/http/task/router.ts。
 * Canvas (副屏) 端点消费 TaskGraphView / TaskNodeDetailView (plan §1.4b)。
 */

import { request } from '@umijs/max';

// ======================== 枚举与类型 ========================

/** 任务生命周期相位 (TaskStatus, §1.1) */
export type TaskStatus =
  | 'intake'
  | 'discussing'
  | 'planned'
  | 'executing'
  | 'validating'
  | 'delivered'
  | 'cancelled'
  | 'hung';

/** 节点运行态 (NodeStatus, §1.2) */
export type NodeStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'partial_failed'
  | 'failed'
  | 'skipped'
  | 'human_required';

export type RunMode = 'single_bot' | 'coop_group' | 'bbs';
export type CollabMode = 'chat' | 'manager_worker' | 'state_machine';
export type EdgeKind =
  | 'dependency'
  | 'conditional'
  | 'fallback'
  | 'parallel_sync';

/** 协作群下钻引用 (SubDagRef, §1.3a) — 任务图谱只持引用,不跟踪 child 态 */
export interface SubDagRefView {
  ref_kind: string;
  bcs_run_id: string;
  group_id: string;
}

export interface AttemptedExecutorView {
  executor_id: string;
  paradigm?: RunMode | null;
  round: number;
  route_class?: string | null;
  trigger?: string | null;
  outcome?: 'pass' | 'fail' | 'partial' | null;
  at?: string | null;
  note?: string;
}

export interface ArtifactView {
  name: string;
  location?: string;
  type?: string;
  text?: string;
}

/** TaskNodeView — state_machine 画布字段超集 (§1.3b) */
export interface TaskNodeView {
  node_id: string;
  display_name: string;
  status: NodeStatus;
  sub_status?: 'awaiting_response' | 'judging' | string | null;
  attempt: number;
  assignee?: string;
  run_mode?: RunMode | null;
  collab_mode?: CollabMode | null;
  started_at?: string | null;
  completed_at?: string | null;
  is_final_output?: boolean;
  attempted_executors?: AttemptedExecutorView[];
  artifacts?: ArtifactView[];
  acceptance_result?: string | null;
  targets_acceptance?: { kind: string; properties: Record<string, any> }[];
  sub_dag_ref?: SubDagRefView | null;
  properties?: Record<string, any>;
}

export interface TaskEdgeView {
  edge_id: string;
  from_node: string;
  to_node: string;
  kind: EdgeKind;
  outcome?: string | null;
  guard?: string | null;
}

/** TaskGraphView — 副屏动态 DAG 唯一数据契约 (§1.4b) */
export interface TaskGraphView {
  task_id: string;
  root_phase: TaskStatus;
  graph_status: string;
  loop_round: number;
  definition_meta?: Record<string, any> | null;
  nodes: TaskNodeView[];
  edges: TaskEdgeView[];
}

/** TaskNodeDetailView — 点节点看详情 (§1.4b, = TaskNodeView + 投递/验收细节) */
export type TaskNodeDetailView = TaskNodeView & {
  instruction?: string | null;
};

export interface TaskCreatedResponse {
  task_id: string;
  status: string;
  seq: number;
}

// ======================== API ========================

const BASE = '/api/tasks';

/** 创建任务 (FR-OBS-11: backend 发 <AixUI panel> 触发副屏弹出) */
export async function createTask(body: {
  title: string;
  source?: string;
  background?: string;
}) {
  return request<TaskCreatedResponse>(BASE, {
    method: 'POST',
    data: body,
  });
}

/** 顶层动态 DAG 快照 (副屏画布主数据) */
export function getTaskGraph(taskId: string) {
  return request<TaskGraphView>(`${BASE}/${taskId}/graph`, { method: 'GET' });
}

/** 节点执行详情 (点节点弹窗) */
export function getNodeDetail(taskId: string, nodeId: string) {
  return request<TaskNodeDetailView>(`${BASE}/${taskId}/nodes/${nodeId}`, {
    method: 'GET',
  });
}

/** 协作群节点下钻 (路 A: live SM run graph → SmGraphAdapter 映射) */
export function getSubDag(taskId: string, nodeId: string) {
  return request<TaskGraphView>(`${BASE}/${taskId}/nodes/${nodeId}/sub-dag`, {
    method: 'GET',
  });
}

/** approve 计划 → Scheduler.start (PLANNED→EXECUTING + build DAG) */
export function approveTask(taskId: string) {
  return request<any>(`${BASE}/${taskId}/approve`, { method: 'POST' });
}

/** 驱动一次 Scheduler tick */
export function tickTask(taskId: string) {
  return request<any>(`${BASE}/${taskId}/tick`, { method: 'POST' });
}