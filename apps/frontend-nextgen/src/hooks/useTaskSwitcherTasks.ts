import type { TaskListItem } from '@/domain/tasks/models';
import { TASK_STATUS_TONES } from '@/assets/TaskPanel/tokens';
import type { TaskSwitcherItem } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { isEnvelopeFailure, listMyTasks, normalizeMyTaskPage } from '@/services/myTask';
import { normalizeTaskStatus } from '@/shared/taskStatus';
import { useCallback, useEffect, useRef, useState } from 'react';

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
 * 多任务切换必须以当前任务的主会话为边界，不能把用户的全部任务混进下拉列表。
 * 当前任务没有会话标识时只返回自身，避免把所有无会话历史任务错误聚合在一起。
 */
export function filterTasksForCurrentConversation(
  records: TaskListItem[],
  currentTaskId: string,
): TaskListItem[] {
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
  const title =
    record.task_spec?.context?.title?.trim() ||
    record.task_spec?.goal?.objective?.trim() ||
    record.task_id;
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

/**
 * 多任务切换条数据源(spec §3.2:listTasks 现有 list API)。
 * 失败静默空列表;enabled=false 或缺 userId 不发查询;60s 慢刷新。
 */
export function useTaskSwitcherTasks(
  userId?: string,
  options?: { enabled?: boolean; currentTaskId?: string },
): { items: TaskSwitcherItem[]; loading: boolean } {
  const enabled = options?.enabled !== false;
  const currentTaskId = options?.currentTaskId?.trim() ?? '';
  const [items, setItems] = useState<TaskSwitcherItem[]>([]);
  const [loading, setLoading] = useState(false);
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    if (!enabled || !userId || !currentTaskId) {
      setItems([]);
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
      const records = Array.isArray(result.data)
        ? result.data
        : normalizeMyTaskPage(result.data).items;
      setItems(filterTasksForCurrentConversation(records, currentTaskId).map(toSwitcherItem));
    } catch {
      // 契约:失败静默为空列表,切换条不出现,不打扰主流程
      if (requestId === requestIdRef.current) setItems([]);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [currentTaskId, enabled, userId]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), SWITCHER_REFRESH_MS);
    return () => {
      clearInterval(timer);
      requestIdRef.current += 1;
    };
  }, [refresh]);

  return { items, loading };
}
