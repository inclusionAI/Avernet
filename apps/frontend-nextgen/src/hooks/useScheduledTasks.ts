import { scheduledTasksService } from '@/services/scheduledTasks';
import { useMemo } from 'react';

// useScheduledTasks 只做 React 胶水层，后续页面状态和副作用从这里进入 Service。
export function useScheduledTasks() {
  return useMemo(() => scheduledTasksService.getOverview(), []);
}
