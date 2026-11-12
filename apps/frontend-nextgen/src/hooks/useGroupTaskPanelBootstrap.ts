import type { TaskPanelBootstrapHook } from '@/extensions/contracts/taskPanelBootstrap';
import { createGroupTaskHistoryReader } from '@/services/tasks/panel/taskPanelHistoryAdapter';
import { useMemo } from 'react';
import { useSessionTaskPanelBootstrap } from './useSessionTaskPanelBootstrap';

/** Open 装配持有任务历史读取器；collab 的空槽位不会加载任务运行时。 */
export const useGroupTaskPanelBootstrap: TaskPanelBootstrapHook = (options) => {
  const history = useMemo(() => createGroupTaskHistoryReader(null), [options.sessionId]);
  return useSessionTaskPanelBootstrap({ ...options, kind: 'group', history });
};
