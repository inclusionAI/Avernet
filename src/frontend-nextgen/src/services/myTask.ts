import type { TaskListItem } from '@/domain/tasks/models';
import { listTasks as listTaskController } from '@/services/backendApi/tasks/taskController';

export interface ListMyTaskParams {
  owner_user_id: string;
  page?: number;
  page_size?: number;
}

export interface ListMyTaskResult {
  code?: number;
  message?: string;
  data?: TaskListItem[] | null;
}

export function listMyTasks(params: ListMyTaskParams, baseUrl?: string): Promise<ListMyTaskResult> {
  return listTaskController(params, baseUrl) as Promise<ListMyTaskResult>;
}
