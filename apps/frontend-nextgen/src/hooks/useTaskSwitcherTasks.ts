import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { resolveNodeSessionTarget } from '@/assets/TaskPanel/sessionResolution';
import { mapDashboard } from '@/assets/TaskPanel/taskPanelMapper';
import { TASK_STATUS_TONES } from '@/assets/TaskPanel/tokens';
import type { TaskSwitcherItem } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import type { TaskListItem } from '@/domain/tasks/models';
import { dashboardTask } from '@/services/backendApi/tasks/taskController';
import { isEnvelopeFailure, listMyTasks, normalizeMyTaskPage } from '@/services/myTask';
import { normalizeTaskStatus } from '@/shared/taskStatus';
import type { TaskNavTarget } from '@/stores/taskSidePanelStore';
import { useTaskUpdateAttentionStore } from '@/stores/taskUpdateAttentionStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/** 刷新间隔:任务切换条数据刷新慢节拍(主数据流由副屏 5s 轮询承担)。 */
const SWITCHER_REFRESH_MS = 60_000;

/**
 * 解析任务所属的主会话。新任务优先读取 execution_config，历史任务兼容旧 extend_props。
 */
export function resolveTaskMainSessionId(record: TaskListItem): string | null {
  const modern = record.execution_config?.main_session_id?.trim();
  if (modern) return modern;
  const legacy = record.task_spec?.context?.extend_props?.teamclaw_context?.main_session_id?.trim();
  return legacy || null;
}

/**
 * 用户手动切换任务时，导航目标必须来自该任务第一个步骤节点：
 * - 节点时间窗让左侧会话定位到该步骤开始处的消息；
 * - 节点自身会话优先，缺失时沿用 TaskPanel 既有节点会话解析兜底。
 */
export async function buildTaskConversationNavigationTarget(record: TaskListItem): Promise<TaskNavTarget | null> {
  try {
    const result = await dashboardTask(record.task_id);
    if (isEnvelopeFailure(result) || !result.data) return null;
    const task = mapDashboard(result.data as TaskDashboardResponse);
    const firstNode = task.nodes[0];
    if (!firstNode) return null;

    const sessionId = resolveTaskMainSessionId(record);
    const sourceGroupId =
      record.execution_config.source_group_id?.trim() ||
      record.task_spec.context.extend_props.teamclaw_context?.source_group_id?.trim() ||
      null;
    const target = resolveNodeSessionTarget(firstNode, {
      ...task,
      mainSessionId: task.mainSessionId ?? sessionId,
      sourceGroupId: task.sourceGroupId ?? sourceGroupId,
    });
    return target ? { ...target, taskId: record.task_id, nodeId: firstNode.id } : null;
  } catch {
    // 找不到第一步骤时不能退回“整任务时间窗”，否则会定位到错误会话/错误消息。
    return null;
  }
}

/**
 * 任务切换必须以当前任务的主会话为边界，不能把用户的全部任务混进下拉列表。
 * 当前任务没有会话标识时只返回自身，避免把所有无会话历史任务错误聚合在一起。
 */
export function filterTasksForCurrentConversation(records: TaskListItem[], currentTaskId: string): TaskListItem[] {
  const current = records.find((record) => record.task_id === currentTaskId);
  if (!current) return [];
  const currentSessionId = resolveTaskMainSessionId(current);
  if (!currentSessionId) return [current];
  return records.filter((record) => resolveTaskMainSessionId(record) === currentSessionId);
}

/**
 * 横条展示状态词表(三桶):活跃(执行中类) → running;未启动类 → pending;终态(含失败/取消)→ done。
 * list 接口返回运行时态(PENDING/RUNNING…)与产品态混布,先经 normalizeTaskStatus 收敛。
 * 导出供其它持有 TaskListItem 列表的宿主(如 MyTask 抽屉)复用同一映射。
 */
export function toSwitcherItem(record: TaskListItem): TaskSwitcherItem {
  const productStatus = normalizeTaskStatus(record.status);
  const statusLabel = TASK_STATUS_TONES[productStatus]?.label ?? record.status;
  const title = record.task_spec?.context?.title?.trim() || record.task_spec?.goal?.objective?.trim() || record.task_id;
  switch (productStatus) {
    case 'EXECUTING':
    case 'REVIEWING':
      return { taskId: record.task_id, title, status: 'running', statusLabel };
    case 'DRAFTING':
    case 'DEFINED':
      // 待启动桶定性(极简三桶,不区分定义中/待执行细分)
      return { taskId: record.task_id, title, status: 'pending', statusLabel: '待开始' };
    default:
      // DONE/FAILED/CANCELLED 及未知态均归终态桶(横条语义:未活跃即可回顾)
      return { taskId: record.task_id, title, status: 'done', statusLabel };
  }
}

interface TaskUpdateSnapshot {
  status: unknown;
  gmtModified: string;
}

/**
 * 任务切换条数据源(spec §3.2:listTasks 现有 list API)。
 * 除渲染同会话任务外，还比较慢刷新快照：非焦点任务 status/gmt_modified 变化仅打点提醒，
 * 不改变 activeTaskId,满足“事件唤回不抢焦”。
 * 失败静默空列表;enabled=false 或缺 userId 不发查询;60s 慢刷新。
 */
export function useTaskSwitcherTasks(
  userId?: string,
  options?: { enabled?: boolean; currentTaskId?: string },
): { items: TaskSwitcherItem[]; records: TaskListItem[]; loading: boolean } {
  const enabled = options?.enabled !== false;
  const currentTaskId = options?.currentTaskId?.trim() ?? '';
  const [items, setItems] = useState<TaskSwitcherItem[]>([]);
  const [records, setRecords] = useState<TaskListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const requestIdRef = useRef(0);
  const previousSnapshotsRef = useRef<Map<string, TaskUpdateSnapshot> | null>(null);
  const attentionByTask = useTaskUpdateAttentionStore((state) => state.attentionByTask);
  const markTaskUpdates = useTaskUpdateAttentionStore((state) => state.markTaskUpdates);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    if (!enabled || !userId || !currentTaskId) {
      setItems([]);
      setRecords([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      // list API 缺少 main_session_id 查询条件，使用历史兼容的全量数组形态后在前端按会话过滤。
      const result = await listMyTasks({ user_id: userId });
      if (requestId !== requestIdRef.current) return;
      if (isEnvelopeFailure(result) || !result.data) {
        throw new Error(result.message || '任务列表加载失败');
      }
      const records = Array.isArray(result.data) ? result.data : normalizeMyTaskPage(result.data).items;
      const conversationRecords = filterTasksForCurrentConversation(records, currentTaskId);
      const previousSnapshots = previousSnapshotsRef.current;
      // 首次加载只建立基线，不把历史任务误判为进展。
      const updatedTaskIds =
        previousSnapshots === null
          ? []
          : conversationRecords.flatMap((record) => {
              const previous = previousSnapshots.get(record.task_id);
              if (!previous || record.task_id === currentTaskId) return [];
              const status = normalizeTaskStatus(record.status);
              return status !== previous.status || record.gmt_modified !== previous.gmtModified ? [record.task_id] : [];
            });
      previousSnapshotsRef.current = new Map(
        conversationRecords.map((record) => [
          record.task_id,
          { status: normalizeTaskStatus(record.status), gmtModified: record.gmt_modified },
        ]),
      );
      if (updatedTaskIds.length > 0) markTaskUpdates(updatedTaskIds);
      setRecords(conversationRecords);
      setItems(conversationRecords.map(toSwitcherItem));
    } catch {
      // 契约:失败静默为空列表,切换条不出现,不打扰主流程
      if (requestId === requestIdRef.current) {
        setItems([]);
        setRecords([]);
      }
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [currentTaskId, enabled, markTaskUpdates, userId]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), SWITCHER_REFRESH_MS);
    return () => {
      clearInterval(timer);
      requestIdRef.current += 1;
    };
  }, [refresh]);

  // 徽标状态单独派生，避免 attention store 更新触发一次额外 list 轮询。
  const displayItems = useMemo(
    () =>
      items.map((item) => ({
        ...item,
        hasUnhandledUpdate: Boolean(attentionByTask[item.taskId]),
      })),
    [attentionByTask, items],
  );

  return { items: displayItems, records, loading };
}
