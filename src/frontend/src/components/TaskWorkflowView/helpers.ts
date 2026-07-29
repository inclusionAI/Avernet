/**
 * TaskWorkflowView 画布辅助 — 状态色映射 (§1.3c) + 模态标签 (§1.3b 超集)。
 *
 * 状态色与现有 bcsPanel/StateMachineRunView 视觉对齐:运行=蓝、完成=绿、
 * 失败/部分=红、挂起人工=橙、跳过=灰。模态标签按 run_mode/collab_mode 渲染。
 */
import type {
  CollabMode,
  NodeStatus,
  RunMode,
  TaskStatus,
} from '@/services/backend-api/TaskController';

export const NODE_STATUS_COLOR: Record<NodeStatus, string> = {
  pending: '#9ca3af', // gray — 未解锁/待派发
  running: '#2563eb', // blue — 执行中
  done: '#16a34a', // green — 完成
  partial_failed: '#f59e0b', // amber — 验收不过待重路由
  failed: '#dc2626', // red — 执行报错(重试耗尽)
  skipped: '#9ca3af', // gray — 拆解后父委托 sibling / 分支裁剪
  human_required: '#ea580c', // orange — 等人工确权(任务图谱独有)
};

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  pending: '待执行',
  running: '执行中',
  done: '已完成',
  partial_failed: '部分失败',
  failed: '失败',
  skipped: '已跳过',
  human_required: '待人工',
};

export const ROOT_PHASE_COLOR: Record<TaskStatus, string> = {
  intake: '#6b7280',
  discussing: '#6366f1',
  planned: '#0ea5e9',
  executing: '#2563eb',
  validating: '#7c3aed',
  delivered: '#16a34a',
  cancelled: '#9ca3af',
  hung: '#dc2626',
};

export const RUN_MODE_LABEL: Record<RunMode, string> = {
  single_bot: '单Bot',
  coop_group: '协作群',
  bbs: 'BBS广场',
};

export const COLLAB_MODE_LABEL: Record<CollabMode, string> = {
  chat: '自由聊',
  manager_worker: '管理者-工人',
  state_machine: '状态机',
};

/** 节点是否为协作群模态(下钻入口) */
export function isCoopGroupNode(node: {
  run_mode?: RunMode | null;
  sub_dag_ref?: { bcs_run_id: string } | null;
}): boolean {
  return (
    node.run_mode === 'coop_group' ||
    (node.sub_dag_ref != null && !!node.sub_dag_ref.bcs_run_id)
  );
}

export function nodeBadge(node: {
  run_mode?: RunMode | null;
  collab_mode?: CollabMode | null;
}): { mode: string; sub?: string } {
  const mode = node.run_mode ? RUN_MODE_LABEL[node.run_mode] : '';
  const sub =
    node.collab_mode && node.run_mode === 'coop_group'
      ? COLLAB_MODE_LABEL[node.collab_mode]
      : undefined;
  return { mode, sub };
}