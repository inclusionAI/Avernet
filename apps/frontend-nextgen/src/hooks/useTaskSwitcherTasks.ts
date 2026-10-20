import type { TaskListItem } from '@/domain/tasks/models';
import { TASK_STATUS_TONES } from '@/assets/TaskPanel/tokens';
import type { TaskSwitcherItem } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { isEnvelopeFailure, listMyTasks, normalizeMyTaskPage } from '@/services/myTask';
import { normalizeTaskStatus } from '@/shared/taskStatus';
import { useCallback, useEffect, useRef, useState } from 'react';

/** 刷新间隔:任务切换条数据刷新慢节拍(主数据流由副屏 5s 轮询承担)。 */
const SWITCHER_REFRESH_MS = 60_000;

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
  options?: { enabled?: boolean },
): { items: TaskSwitcherItem[]; loading: boolean } {
  const enabled = options?.enabled !== false;
  const [items, setItems] = useState<TaskSwitcherItem[]>([]);
  const [loading, setLoading] = useState(false);
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    if (!enabled || !userId) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await listMyTasks({ user_id: userId, page: 1, page_size: 50 });
      if (requestId !== requestIdRef.current) return;
      if (isEnvelopeFailure(result) || !result.data) {
        throw new Error(result.message || '任务列表加载失败');
      }
      const normalized = normalizeMyTaskPage(result.data, 1, 50);
      setItems((normalized.items ?? []).map(toSwitcherItem));
    } catch {
      // 契约:失败静默为空列表,切换条不出现,不打扰主流程
      if (requestId === requestIdRef.current) setItems([]);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [enabled, userId]);

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
