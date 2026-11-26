import type { TaskListItem } from '@/domain/tasks/models';
import {
  isEnvelopeFailure,
  listMyTasks,
  normalizeMyTaskPage,
  runtimeStatusesFromProductFilter,
} from '@/services/myTask';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { UserTaskStatusFilter } from '../userTaskUtils';

/**
 * 我的任务列表查询 Hook：服务端分页 + 服务端状态过滤。
 * statusFilter 是产品态 Tab(all/DRAFTING/DEFINED/EXECUTING/REVIEWING/DONE/FAILED/CANCELLED),
 * 这里反查为后端运行时态集合(逗号分隔多值,SQL IN 过滤)。空集合(DRAFTING 等运行时不产生的状态)
 * 直接短路返回空,不查后端。状态/分页变化均触发重新查询。
 */
export function useMyTaskTasks(
  ownerUserId: string,
  page: number,
  pageSize: number,
  statusFilter: UserTaskStatusFilter,
  enabled = true,
) {
  const [taskRecords, setTaskRecords] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    if (!enabled || !ownerUserId) {
      setTaskRecords([]);
      setTotal(0);
      setError(null);
      setLoading(false);
      return;
    }
    // 产品态 Tab → 后端运行时态集合:'all' → undefined(不过滤)；空集合(DRAFTING 等)→ 短路空。
    const runtimeStatuses = runtimeStatusesFromProductFilter(statusFilter);
    if (runtimeStatuses !== undefined && runtimeStatuses.length === 0) {
      setTaskRecords([]);
      setTotal(0);
      setError(null);
      setLoading(false);
      return;
    }
    const statusParam = runtimeStatuses ? runtimeStatuses.join(',') : undefined;
    setLoading(true);
    setError(null);
    try {
      // 服务端分页 + 服务端状态过滤:page/page_size 必传；status 为运行时态逗号串(all 时不传)。
      const result = await listMyTasks({
        user_id: ownerUserId,
        page,
        page_size: pageSize,
        status: statusParam,
      });
      if (requestId !== requestIdRef.current) return;
      if (isEnvelopeFailure(result) || !result.data) {
        throw new Error(result.message || '用户任务列表加载失败');
      }
      const normalized = normalizeMyTaskPage(result.data, page, pageSize);
      setTaskRecords(normalized.items);
      setTotal(normalized.total);
    } catch (err) {
      if (requestId !== requestIdRef.current) return;
      const message = err instanceof Error ? err.message : '用户任务列表加载失败';
      setError(message);
      setTaskRecords([]);
      setTotal(0);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [enabled, ownerUserId, page, pageSize, statusFilter]);

  useEffect(() => {
    void refresh();
    return () => {
      requestIdRef.current += 1;
    };
  }, [refresh]);

  return { taskRecords, total, loading, error, refresh };
}
