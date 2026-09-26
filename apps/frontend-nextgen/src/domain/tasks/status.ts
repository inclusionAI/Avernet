/** 任务状态归一化：按后端运行时状态映射为产品层任务状态。 */
import { normalizeTaskStatus as normalizeSharedTaskStatus } from '@/shared/taskStatus';
import type { TaskStatus } from './models';

/**
 * get_task_dashboard / tasks/list 在后端切换期间可能返回两套状态：
 * - 产品层：DRAFTING / DEFINED / EXECUTING / REVIEWING / DONE / FAILED / CANCELLED
 * - 旧运行时：PENDING / PLANNING / RUNNING / HUNG / DONE / FAILED
 */
export function normalizeTaskStatus(status: unknown): TaskStatus {
  return normalizeSharedTaskStatus(status);
}
