import { backendRequest } from '../httpClient';

import type { WorkerTaskStatusView } from '@/domain/groupTasks/statusProjection';

/** BCS task handles are bare JSON, with no backend envelope. */
export function queryWorkerTaskStatuses(taskRefs: string[]) {
  return backendRequest<{ tasks: WorkerTaskStatusView[] }>(
    '/openapi/v1/collaboration/worker-tasks/status',
    { method: 'POST', data: { intent_ids: taskRefs }, injectUserId: false },
  );
}

export function cancelWorkerTask(taskRef: string) {
  return backendRequest<WorkerTaskStatusView>(
    '/openapi/v1/collaboration/worker-tasks/cancel',
    { method: 'POST', data: { intent_id: taskRef, reason: '用户停止任务' }, injectUserId: false },
  );
}
