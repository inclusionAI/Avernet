// 完整发现 hook：保留任务调用方；Bot/群页面直接消费无任务依赖的公共 hook。
import type { SquareResource } from '@/domain/collaborationSquare/types';
import { usePublicDiscovery } from './usePublicDiscovery';
import { useCollaborationSquareTask } from './useCollaborationSquareTask';

export function useCollaborationSquare(resource: SquareResource) {
  const publicDiscovery = usePublicDiscovery(resource);
  const taskView = useCollaborationSquareTask(resource);
  return {
    ...publicDiscovery,
    ...(resource === 'task' ? { ...taskView.list, reload: taskView.list.load } : {}),
    openTaskDetail: taskView.openTaskDetail,
    closeTaskDetail: taskView.closeTaskDetail,
  };
}
