import type { TaskListItem } from '@/domain/tasks/models';
import { listMyTasks } from '@/services/myTask';
import { TASK_LIST_PAGE_SIZE } from '@/services/tasks/taskConfig';
import { useCallback, useEffect, useState } from 'react';

export function useMyTaskTasks(ownerUserId: string) {
  const [taskRecords, setTaskRecords] = useState<TaskListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!ownerUserId) {
      setTaskRecords([]);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await listMyTasks({ owner_user_id: ownerUserId, page: 1, page_size: TASK_LIST_PAGE_SIZE });
      if (result.code !== 200000 || !Array.isArray(result.data)) {
        throw new Error(result.message || '用户任务列表加载失败');
      }
      setTaskRecords(result.data);
    } catch (err) {
      const message = err instanceof Error ? err.message : '用户任务列表加载失败';
      setError(message);
      setTaskRecords([]);
    } finally {
      setLoading(false);
    }
  }, [ownerUserId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { taskRecords, loading, error, refresh };
}
