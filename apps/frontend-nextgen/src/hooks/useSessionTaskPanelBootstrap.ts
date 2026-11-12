import type { TaskListItem } from '@/domain/tasks/models';
import { resolveTaskMainSessionId } from '@/domain/tasks/taskSession';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import type { PanelHandle, PanelTab } from '@tc-chat/core';
import { useSharedTaskPanelRecovery } from './useSharedTaskPanelRecovery';
import type { ConversationRef, HistoryReader } from '@avernet-assets/task-panel';

type PanelRef = React.RefObject<PanelHandle | null>;
/**
 * 解析会话归属人工号（session_id 末尾 :user:<owner>）。
 *
 * 任务列表兜底要按会话归属人查询；但打开后的 panel 交互身份仍需用当前登录人。
 * 无 :user: 段（测试/旧会话）时退回当前登录人。
 */
export function resolveSessionOwnerUserId(sessionId: string | null, fallbackUserId: string | null): string | null {
  const owner = sessionId?.match(/:user:([^:]+)$/)?.[1]?.trim();
  return owner || fallbackUserId;
}

function parseTaskTime(value: string | undefined): number {
  // list API 返回 `YYYY-MM-DD HH:mm:ss`；补 T 兼容 Safari。
  if (!value?.trim()) return Number.NEGATIVE_INFINITY;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function taskSortTime(record: TaskListItem): number {
  return parseTaskTime(record.gmt_modified) || parseTaskTime(record.gmt_create) || Number.NEGATIVE_INFINITY;
}

/** 选择会话内最近更新的任务；无有效时间时保持列表返回顺序。 */
export function findLatestTaskForSession(records: TaskListItem[], sessionId: string): TaskListItem | null {
  let latest: TaskListItem | null = null;
  for (const record of records) {
    if (resolveTaskMainSessionId(record) !== sessionId) continue;
    if (!latest || taskSortTime(record) > taskSortTime(latest)) latest = record;
  }
  return latest;
}

export function buildRelayTaskPanelTab(
  taskId: string,
  userId: string,
  title?: string,
): PanelTab {
  return {
    id: `task-${taskId}`,
    title: title ?? '任务',
    type: 'taskPanel.TaskLoopView',
    closable: true,
    defaultWidth: resolveTaskPanelDefaultWidth(),
    params: {
      taskId,
      apiBaseUrl: '',
      bcsBaseUrl: '',
      userId,
      taskApiBase: TASK_READ_API_BASE,
    },
  };
}

export function buildSessionTaskPanelTab(record: TaskListItem, userId: string): PanelTab {
  const title =
    record.task_spec?.context?.title?.trim() ||
    record.task_spec?.goal?.objective?.trim() ||
    `任务 ${record.task_id}`;
  return {
    id: `task-${record.task_id}`,
    title,
    type: 'taskPanel.TaskLoopView',
    closable: true,
    defaultWidth: resolveTaskPanelDefaultWidth(),
    params: {
      taskId: record.task_id,
      apiBaseUrl: '',
      bcsBaseUrl: '',
      userId,
    },
  };
}

/**
 * 进入会话时用任务执行记录或任务列表反查任务并打开副屏。
 *
 * by_session 返回同会话的全部轻量 task ref：默认打开最新一个；显式 task intent
 * 优先；AixUI 已经触发展示时不允许 by_session 抢焦点；普通会话保持 list 兜底。
 */
export function useSessionTaskPanelBootstrap(options: {
  sessionId: string | null;
  kind?: ConversationRef['kind'];
  userId: string | null;
  panelRef: PanelRef;
  enabled?: boolean;
  history?: HistoryReader | null;
}) {
  return useSharedTaskPanelRecovery({
    conversation: options.sessionId ? { sessionId: options.sessionId, kind: options.kind ?? (options.sessionId.startsWith('bcs_grp_') ? 'group' : 'bot') } : null,
    userId: options.userId, panelRef: options.panelRef,
    enabled: options.enabled !== false, history: options.history ?? null,
  });
}
export default useSessionTaskPanelBootstrap;
